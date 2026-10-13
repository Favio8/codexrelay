"""Installer handoff: freeze new jobs, preserve state, and relocate only owned MCP entries.

Also executed as a standalone file by an older bundled Python during preflight.
"""
import json
import os
import sys
from pathlib import Path
import tomlkit
from codexrelay.state import Store
from codexrelay import integration


def save_plan(path, plan):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding='utf-8')
    os.replace(temporary, path)


def prepare(path, plan):
    store = Store(plan['state_directory'])
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        store._reap(db)
        if db.execute('SELECT 1 FROM tasks WHERE active=1').fetchone():
            raise ValueError('存在正在执行的委托，请结束任务后再升级。')
        plan['original_enabled'] = bool(json.loads(db.execute("SELECT value FROM settings WHERE key='enabled'").fetchone()[0]))
        plan['prepared'] = True
        save_plan(path, plan)
        db.execute("UPDATE settings SET value='false' WHERE key='enabled'")


def restore(plan):
    if plan.get('prepared'):
        Store(plan['state_directory']).set_setting('enabled', plan['original_enabled'])


def finish(path, plan):
    from codexrelay import __version__
    if plan.get('version') and plan['version'] != __version__:
        raise ValueError('执行引擎版本未更新，升级未完成。')
    store = Store(plan['state_directory'])
    snapshot = store.snapshot()
    homes = {integration.codex_dir()}
    for key in snapshot:
        for prefix in ('integration_registration:', 'integration_backup:'):
            if key.startswith(prefix):
                homes.add(Path(key[len(prefix):]))
    updated = []
    for home in sorted(homes, key=str):
        config_path = home / 'config.toml'
        if not config_path.is_file():
            continue
        previous = integration.read(config_path)
        config = tomlkit.parse(integration.decode(previous))
        entry = config.get('mcp_servers', {}).get('codexrelay')
        if not entry or not integration.owned(entry, store, home):
            continue
        command = str(Path(sys.executable).resolve())
        if not integration.same_executable(entry.get('command', ''), command):
            entry['command'] = command
            integration.replace_checked(config_path, previous, tomlkit.dumps(config).encode('utf-8'))
            updated.append(str(config_path))
        store.set_setting('integration_registration:' + str(home.resolve()), {'command': command})
    plan['updated_mcp_configs'] = updated
    restore(plan)
    plan['finished'] = True
    save_plan(path, plan)


def main():
    action, filename = sys.argv[1:3]
    path = Path(filename)
    plan = json.loads(path.read_text(encoding='utf-8-sig'))
    try:
        if action == 'prepare':
            prepare(path, plan)
        elif action == 'finish':
            finish(path, plan)
        elif action == 'abort':
            restore(plan)
        else:
            raise ValueError('未知升级操作')
    except Exception as exc:
        plan['error'] = str(exc)
        save_plan(path, plan)
        raise
    print(json.dumps({'ok': True, 'action': action}))


if __name__ == '__main__':
    main()
