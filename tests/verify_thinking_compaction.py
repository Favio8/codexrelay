"""Official API protocol regression. Synthetic history, real requests; no writes/tools."""
import copy
import json
from openai import OpenAI,APIError
from codexrelay.service import Service
from deepseek_mcp.history_compaction import compact_history,ARCHIVE
from deepseek_mcp.provider_child import _error_summary


def main():
    service=Service();provider=service.store.provider()
    key=service.vault.get(provider['credential_id'])
    tools=[{'type':'function','function':{'name':'relay_probe','description':'Return a small test number','parameters':{'type':'object','properties':{},'additionalProperties':False}}}]
    initial=[{'role':'user','content':'Protocol test only. Call relay_probe to obtain a number, then summarize the supplied tool results. Do not invent a tool result.'}]
    with OpenAI(api_key=key,base_url=provider['base_url'],max_retries=0,timeout=30) as client:
        reply=client.chat.completions.create(model=provider['flash_model'],messages=initial,tools=tools,max_tokens=1024,reasoning_effort='high')
        assistant=reply.choices[0].message.model_dump(exclude_none=True)
        assert assistant.get('tool_calls') and 'reasoning_content' in assistant
        # Build a test fixture using the opaque assistant payload, preserving the
        # reasoning field rather than printing or interpreting it.
        history=list(initial)
        for index in range(7):
            message=copy.deepcopy(assistant)
            for ordinal,call in enumerate(message['tool_calls']):call['id']=f'fixture_{index}_{ordinal}'
            history.append(message)
            history.extend({'role':'tool','tool_call_id':call['id'],'content':f'Fixture result {index}: '+'x'*24000} for call in message['tool_calls'])
        old=[history[0],{'role':'assistant','content':ARCHIVE+'\nEarlier fixture tool outputs omitted.'},*history[-8:]]
        rejected=False
        try:client.chat.completions.create(model=provider['flash_model'],messages=old,tools=tools,max_tokens=1024,reasoning_effort='high')
        except APIError as error:
            summary,_=_error_summary(error)
            rejected='reasoning_history' in summary and error.status_code==400
        assert rejected,'Old synthetic assistant archive should reproduce the reasoning-history 400'
        fixed,count=compact_history(history)
        assert count>0
        response=client.chat.completions.create(model=provider['flash_model'],messages=fixed,tools=tools,max_tokens=1024,reasoning_effort='high')
        assert response.choices[0].message.content or response.choices[0].message.tool_calls
    print(json.dumps({'ok':True,'provider':'official DeepSeek','history':'synthetic protocol fixture','checks':['old assistant archive reproduces HTTP 400 reasoning-history failure','fixed compaction retains all opaque assistant reasoning','same provider accepts fixed history'],'compacted_exchanges':count}))


if __name__=='__main__':main()
