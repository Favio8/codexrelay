"""Opt-in real official API smoke test; uses a temporary Windows credential.

Requires DEEPSEEK_API_KEY in this process. Never prints or persists that value.
"""
import asyncio
import hashlib
import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from codexrelay.service import Service
from codexrelay.state import Store

ROOT = Path(__file__).resolve().parents[1]


async def run():
    secret = os.environ.get("DEEPSEEK_API_KEY")
    if not secret:
        raise RuntimeError("DEEPSEEK_API_KEY is required for opt-in live verification")
    base = ROOT / ".tmp" / ("live-" + uuid.uuid4().hex[:10])
    workspace = base / "workspace"
    workspace.mkdir(parents=True)
    marker = "REAL_FILE_" + uuid.uuid4().hex
    (workspace / "marker.txt").write_text(marker, encoding="utf-8")
    (workspace / "calculator.py").write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")
    (workspace / "test_calculator.py").write_text("import unittest\nfrom calculator import add\nclass Tests(unittest.TestCase):\n    def test_add(self): self.assertEqual(add(2, 3), 5)\n    def test_negative(self): self.assertEqual(add(-2, 3), 1)\nif __name__ == '__main__': unittest.main()\n", encoding="utf-8")
    service = Service(Store(base / "state"))
    saved = service.save_provider(name="Official live verification", kind="deepseek", base_url="https://api.deepseek.com", flash_model="deepseek-flash", pro_model="deepseek-v4-pro", reasoning_effort="high", api_key=secret)
    credential = service.store.provider(saved["id"])["credential_id"]
    env = {k:v for k,v in os.environ.items() if k not in {"DEEPSEEK_API_KEY", "OPENAI_API_KEY", "PYTHONPATH", "PYTHONHOME"}}
    env["CODEXRELAY_HOME"] = str(service.store.directory)
    params = StdioServerParameters(command=sys.executable, args=["-I", "-X", "utf8", "-m", "codexrelay", "mcp"], cwd=str(workspace), env=env)
    report = {"provider":"DeepSeek official real API", "workspace":str(workspace),"checks":[]}
    try:
        service.dispatch("set_enabled", {"enabled":True})
        async with stdio_client(params) as (reader,writer):
            async with ClientSession(reader,writer) as client:
                await client.initialize()
                async def call(name, **kwargs):
                    result = await client.call_tool(name, kwargs)
                    if result.isError: raise AssertionError(str(result))
                    return json.loads(result.content[0].text)
                result = await call("delegate_to_deepseek_readonly", task="Read marker.txt using Read and return its exact contents. Do not read any other files.")
                assert result.get("status") == "completed" and marker in json.dumps(result), result
                report["checks"].append("readonly: real model read undisclosed file marker")
                task = "Read calculator.py and test_calculator.py. Fix add to perform addition. Change only calculator.py. Then run tests once using Bash command: " + str(Path(sys.executable)) + " -X utf8 -m unittest -v. Return the outcome. Do not use Git or inspect outside this workspace."
                result = await call("delegate_to_deepseek", task=task)
                assert result.get("status") == "completed", result
                independently = subprocess.run([sys.executable,"-I","-X","utf8","-m","unittest","discover","-s",str(workspace),"-v"],cwd=workspace,capture_output=True,text=True)
                assert independently.returncode == 0, independently.stderr
                report["checks"].append("coding: real model repaired function; host independently reran 2 tests")
                recovery = await call("get_deepseek_recovery")
                assert recovery["pending"], recovery
                ids=[]
                for item in recovery["pending"]:
                    path=workspace/item["path"]
                    assert path.resolve().is_relative_to(workspace.resolve())
                    assert item["path"] == "calculator.py", item
                    assert hashlib.sha256(path.read_bytes()).hexdigest() == item["sha256"]
                    ids.append(item["transaction_id"])
                assert "return a + b" in (workspace/"calculator.py").read_text()
                ack=await call("acknowledge_deepseek_mutations",transaction_ids=ids)
                assert not ack["pending"],ack
                service.dispatch("set_enabled",{"enabled":False})
                blocked=await call("start_deepseek_readonly",task="Read marker.txt")
                assert "DISABLED" in blocked["error"],blocked
                report["checks"].append("same-session disabled + verified mutation acknowledgement")
        report["ok"]=True
    finally:
        service.vault.delete(credential)
        report["temporary_credential_deleted"]=True
        (base/"report.json").write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
        print(json.dumps(report,ensure_ascii=False,indent=2))


if __name__ == "__main__":
    asyncio.run(run())
