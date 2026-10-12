"""Verify the actual adopted registration using its saved credential, without env keys."""
import asyncio
import json
import os
import sys
import uuid
from pathlib import Path
import tomlkit
from mcp import ClientSession,StdioServerParameters
from mcp.client.stdio import stdio_client
from codexrelay.service import Service
from codexrelay.integration import codex_dir


async def run():
    root=Path(__file__).resolve().parents[1]
    service=Service()
    config=tomlkit.parse((codex_dir()/'config.toml').read_text(encoding='utf-8-sig'))
    assert 'deepseek' not in config['mcp_servers']
    entry=config['mcp_servers']['codexrelay']
    provider=service.store.provider()
    assert service.vault.get(provider['credential_id'])
    service.test_provider(provider['id'])
    workspace=root/'.tmp'/('adopted-'+uuid.uuid4().hex[:8]);workspace.mkdir()
    marker='ADOPTED_'+uuid.uuid4().hex
    (workspace/'verify.txt').write_text(marker,encoding='utf-8')
    env={k:v for k,v in os.environ.items() if k not in {'DEEPSEEK_API_KEY','OPENAI_API_KEY','PYTHONPATH','PYTHONHOME'}}
    params=StdioServerParameters(command=entry['command'],args=list(entry['args']),cwd=str(workspace),env=env)
    previous=service.store.setting('enabled')
    try:
        service.dispatch('set_enabled',{'enabled':True})
        async with stdio_client(params) as (r,w):
            async with ClientSession(r,w) as client:
                await client.initialize()
                async def call(name,**kwargs):
                    response=await client.call_tool(name,kwargs)
                    assert not response.isError,response
                    return json.loads(response.content[0].text)
                ping=await call('ping');assert ping['version']=='0.1.1',ping
                result=await call('delegate_to_deepseek_readonly',task='Read verify.txt using the Read tool and return exactly its contents. Do not read other files.')
                assert result['status']=='completed' and marker in json.dumps(result),result
                assert result['result']['budget']['limit']==provider['max_total_tokens']
                service.dispatch('set_enabled',{'enabled':False})
                blocked=await call('start_deepseek_readonly',task='Read verify.txt')
                assert 'DISABLED' in blocked['error'],blocked
                report={'ok':True,'version':ping['version'],'registration':entry['command'],'provider_model':provider['flash_model'],'budget':result['result']['budget'],'checks':['actual adopted registration launches','imported key works without API env variables','real model reads undisclosed marker','same-session managed switch blocks next task'],'desktop_restart_still_required':True}
                (root/'artifacts/evidence/adopted-verification.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
                print(json.dumps(report,ensure_ascii=False,indent=2))
    finally:service.store.set_setting('enabled',previous)


if __name__=='__main__':asyncio.run(run())
