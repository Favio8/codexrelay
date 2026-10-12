"""Real stdio MCP + real upstream tools, against a deterministic local HTTP fixture.

No paid model is used. This checks protocol/tool integration, not model quality.
"""
import asyncio
import hashlib
import json
import os
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from codexrelay.service import Service
from codexrelay.state import Store

ROOT = Path(__file__).resolve().parents[1]


class FixtureHandler(BaseHTTPRequestHandler):
    calls = 0
    def log_message(self, *args):
        pass

    def do_POST(self):
        type(self).calls += 1
        request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        messages = request["messages"]
        task = next((m["content"] for m in messages if m["role"] == "user"), "")
        if "SLOW" in task:
            time.sleep(8)
        tool_messages = [m for m in messages if m["role"] == "tool"]
        if tool_messages:
            message = {"role": "assistant", "content": "FIXTURE_DONE: " + tool_messages[-1]["content"]}
            finish = "stop"
        else:
            writing = "WRITE_FIXTURE" in task
            name = "Write" if writing else "Read"
            args = {"path": "created.txt", "content": "created by real tool\n"} if writing else {"path": "input.txt"}
            if request.get("tools", [{}])[0].get("function", {}).get("name") == "relay_probe":
                name, args = "relay_probe", {}
            message = {"role": "assistant", "content": None, "tool_calls": [{"id": "call_fixture", "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}]}
            finish = "tool_calls"
        response = {"id": "test", "object": "chat.completion", "created": int(time.time()), "model": request["model"],
                    "choices": [{"index": 0, "message": message, "finish_reason": finish}],
                    "usage": {"prompt_tokens": 20, "completion_tokens": 10, "total_tokens": 30}}
        payload = json.dumps(response).encode()
        try:
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass


async def run():
    server = ThreadingHTTPServer(("127.0.0.1", 0), FixtureHandler)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    with tempfile.TemporaryDirectory(prefix="mcp-", dir=ROOT / ".tmp") as folder:
        base = Path(folder)
        workspace = base / "项目 with spaces"
        workspace.mkdir()
        (workspace / "input.txt").write_text("PROBE_314159\n", encoding="utf-8")
        store = Store(base / "state")
        service = Service(store)
        saved = service.save_provider(name="Local protocol fixture", kind="custom", base_url=f"http://127.0.0.1:{server.server_port}/v1", flash_model="fixture", pro_model="fixture")
        service.test_provider(saved["id"])
        env = {k:v for k,v in os.environ.items() if k not in {"DEEPSEEK_API_KEY", "OPENAI_API_KEY", "PYTHONPATH", "PYTHONHOME"}}
        env["CODEXRELAY_HOME"] = str(store.directory)
        params = StdioServerParameters(command=sys.executable, args=["-I", "-X", "utf8", "-m", "codexrelay", "mcp"], cwd=str(workspace), env=env)
        async with stdio_client(params) as (reader, writer):
            async with ClientSession(reader, writer) as client:
                await client.initialize()
                async def call(name, **arguments):
                    reply = await client.call_tool(name, arguments)
                    if reply.isError:
                        raise AssertionError(str(reply))
                    return json.loads(reply.content[0].text)
                listing = await client.list_tools()
                assert len(listing.tools) == 11
                ping = await call("ping")
                assert Path(ping["workspace"]) == workspace
                calls_before = FixtureHandler.calls
                blocked = await call("delegate_to_deepseek_readonly", task="Read input")
                assert "DISABLED" in blocked["error"], blocked
                assert FixtureHandler.calls == calls_before
                service.dispatch("set_enabled", {"enabled": True})
                result = await call("delegate_to_deepseek_readonly", task="Read input")
                assert result["status"] == "completed", result
                assert "PROBE_314159" in json.dumps(result), result
                coding = await call("delegate_to_deepseek", task="WRITE_FIXTURE")
                assert coding["status"] == "completed", coding
                created = workspace / "created.txt"
                assert created.read_text() == "created by real tool\n"
                recovery = await call("get_deepseek_recovery")
                assert len(recovery["pending"]) == 1, recovery
                record = recovery["pending"][0]
                assert record["path"] == "created.txt"
                assert record["sha256"] == hashlib.sha256(created.read_bytes()).hexdigest()
                ack = await call("acknowledge_deepseek_mutations", transaction_ids=[record["transaction_id"]])
                assert not ack["pending"], ack
                started = await call("start_deepseek_readonly", task="SLOW read input")
                assert started["ok"], started
                service.dispatch("set_enabled", {"enabled": False})
                refused = await call("start_deepseek_readonly", task="Read input")
                assert "DISABLED" in refused["error"], refused
                current = store.task(started["job_id"])
                assert not current["cancel_requested"]
                service.dispatch("cancel_task", {"id": started["job_id"]})
                deadline = time.monotonic() + 10
                while time.monotonic() < deadline:
                    final = await call("get_deepseek_result", job_id=started["job_id"])
                    if final["ready"]:
                        break
                    await asyncio.sleep(.2)
                assert final["status"] == "cancelled", final
                assert created.exists()
                print(json.dumps({"ok":True,"tools":len(listing.tools),"provider":"local deterministic fixture, not paid model","checks":["real stdio handshake","same-session hot enable/disable","readonly real file access","coding real Write","mutation hash and exact acknowledgement","background cancellation from GUI control state","disabled mode preserves result/recovery"],"http_calls":FixtureHandler.calls},ensure_ascii=False,indent=2))
    server.shutdown()


if __name__ == "__main__":
    asyncio.run(run())
