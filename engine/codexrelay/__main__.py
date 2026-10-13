import json
import sqlite3
import sys


def rpc_error(exc):
    """Expose actionable local errors without returning arbitrary provider secrets."""
    code = getattr(exc, 'sqlite_errorcode', None)
    base = code & 255 if isinstance(code, int) else None
    details = type(exc).__name__
    if isinstance(exc, sqlite3.Error):
        details += ': ' + str(getattr(exc, 'sqlite_errorname', 'SQLITE_ERROR'))
        if code is not None:
            details += f' ({code})'
        if base in {sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED}:
            return dict(ok=False, error='本地数据暂时被占用，请稍后重试。',
                        code='database_busy', retryable=True, details=details)
        if base in {sqlite3.SQLITE_READONLY, sqlite3.SQLITE_PERM}:
            message = '无法写入本地配置，请检查配置目录权限。'
        elif base == sqlite3.SQLITE_CANTOPEN:
            message = '无法打开本地配置数据库，请检查配置目录。'
        else:
            message = '本地配置数据库异常，请运行诊断并查看详情。'
        return dict(ok=False, error=message, code='database_error', details=details)
    if isinstance(exc, PermissionError):
        return dict(ok=False, error='无法访问本地配置，请检查目录权限。',
                    code='permission_error', details=details)
    return dict(ok=False, error='操作未完成，请运行诊断并查看详情。',
                code='runtime_error', details=details)


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
                    db.execute("DELETE FROM usage_events")
                    db.execute("DELETE FROM projects")
                    db.execute("DELETE FROM instances")
                    db.execute("DELETE FROM settings WHERE key IN ('codex_usage','usage_prices','active_backend','usage_revision')")
                from .codex_usage import clear
                clear(service.store)
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
            response = rpc_error(exc)
        print(json.dumps(response, ensure_ascii=False))
    else:
        print("Usage: python -m codexrelay {rpc|mcp}", file=sys.stderr)
        raise SystemExit(2)


if __name__ == "__main__":
    main()
