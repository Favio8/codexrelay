import json
import sys


def main():
    if len(sys.argv) == 2 and sys.argv[1] in {"uninstall", "purge", "check-idle"}:
        from .service import Service
        from .integration import uninstall
        service = Service()
        try:
            snapshot = service.store.snapshot()
            if any(t["status"] in {"queued", "running", "cancelling"} for t in snapshot["tasks"]):
                raise ValueError("请先结束正在执行的子代理任务")
            if sys.argv[1] == "check-idle":
                return
            for key, value in snapshot.items():
                if key.startswith("integration_backup:") and value:
                    uninstall(service.store, key.split(":", 1)[1])
            if sys.argv[1] == "purge":
                for provider in service.store.providers():
                    service.delete_provider(provider["id"])
                with service.store.connect() as db:
                    db.execute("DELETE FROM tasks")
                    db.execute("DELETE FROM projects")
                    db.execute("DELETE FROM instances")
                for backup in (service.store.directory / "backups").glob("*.dpapi"):
                    backup.unlink()
            print("CodexRelay integration removed; user project files preserved.")
        except Exception as exc:
            print("Cannot uninstall: " + str(exc), file=sys.stderr)
            raise SystemExit(1)
    elif len(sys.argv) == 2 and sys.argv[1] == "mcp":
        from .mcp_server import serve
        serve()
    elif len(sys.argv) == 2 and sys.argv[1] == "rpc":
        from .service import Service
        try:
            request = json.load(sys.stdin)
            result = Service().dispatch(request["method"], request.get("params") or {})
            response = {"ok": True, "data": result}
        except (ValueError, KeyError) as exc:
            response = {"ok": False, "error": str(exc)}
        except Exception as exc:
            response = {"ok": False, "error": "操作失败：" + type(exc).__name__ + "。请检查运行时与配置权限。"}
        print(json.dumps(response, ensure_ascii=False))
    else:
        print("Usage: python -m codexrelay {rpc|mcp}", file=sys.stderr)
        raise SystemExit(2)


if __name__ == "__main__":
    main()
