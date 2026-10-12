"""Run with the installed CodexRelay interpreter after user-authorized migration."""
import hashlib
import json
from pathlib import Path
import tomlkit
from codexrelay.service import Service
from codexrelay.integration import codex_dir,strip_block,BEGIN,END,OLD_BEGIN,OLD_END


def main():
    directory=codex_dir()
    before=tomlkit.parse((directory/'config.toml').read_text(encoding='utf-8-sig')).unwrap()
    rules=(directory/'AGENTS.md').read_text(encoding='utf-8-sig') if (directory/'AGENTS.md').exists() else ''
    untouched_rules=strip_block(strip_block(rules,OLD_BEGIN,OLD_END),BEGIN,END).strip()
    service=Service()
    result=service.dispatch('adopt_legacy',{})
    after=tomlkit.parse((directory/'config.toml').read_text(encoding='utf-8-sig')).unwrap()
    for value in (before,after):
        value.get('mcp_servers',{}).pop('deepseek',None)
        value.get('mcp_servers',{}).pop('codexrelay',None)
    if before!=after:
        raise RuntimeError('Unexpected change outside managed MCP entries; inspect encrypted backup')
    current=(directory/'AGENTS.md').read_text(encoding='utf-8-sig')
    preserved=strip_block(strip_block(current,OLD_BEGIN,OLD_END),BEGIN,END).strip()
    if untouched_rules!=preserved:
        raise RuntimeError('Unexpected change outside managed instruction blocks; inspect encrypted backup')
    report={**result,'unrelated_codex_configuration_preserved':True,'unrelated_agent_rules_preserved':True,
            'rules_digest':hashlib.sha256(preserved.encode()).hexdigest()}
    output=Path(__file__).resolve().parents[1]/'artifacts/evidence/migration-report.json'
    output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
