"""Opt-in, incremental Codex usage import. Persist counters and cursors, never text."""
import hashlib
import json
import time
from collections import Counter
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from .integration import codex_dir
from .usage import daily_window, estimate, amount, add_bounds, validate_price, bump_revision

SCAN_BYTES = 16 * 1024 * 1024
PARSER_VERSION = 2


def schema(db):
    db.executescript('''
        CREATE TABLE IF NOT EXISTS codex_usage_files(path TEXT PRIMARY KEY, identity TEXT, size INTEGER, modified INTEGER, offset INTEGER, state TEXT);
        CREATE TABLE IF NOT EXISTS codex_usage_records(event_key TEXT PRIMARY KEY, occurred_at REAL NOT NULL, kind TEXT NOT NULL, payload TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS codex_usage_date ON codex_usage_records(occurred_at);
        CREATE TABLE IF NOT EXISTS codex_usage_sources(path TEXT NOT NULL,event_key TEXT NOT NULL,PRIMARY KEY(path,event_key));
    ''')


def settings(store):
    return store.setting('codex_usage', {'enabled':False,'directory':str(codex_dir()),'prices':{}})


def configure(store, enabled, directory=None):
    if not isinstance(enabled,bool):
        raise ValueError('统计开关必须是布尔值')
    value = settings(store)
    chosen = codex_dir(directory or value['directory'])
    if enabled and not chosen.is_dir():
        raise ValueError('请选择存在的 Codex 配置目录')
    if str(chosen) != value['directory']:
        clear(store)
    value.update(enabled=enabled,directory=str(chosen))
    store.set_setting('codex_usage',value)
    return value


def clear(store):
    with store.connect() as db:
        schema(db)
        db.execute('BEGIN IMMEDIATE')
        for table in ('codex_usage_sources','codex_usage_records','codex_usage_files'):
            db.execute('DELETE FROM '+table)
    return {'cleared':True}


def save_price(store, provider, model, price):
    if not isinstance(provider,str) or not isinstance(model,str) or not model.strip():
        raise ValueError('请填写来源供应商和实际模型名称')
    value=settings(store)
    value['prices'][json.dumps([provider,model],ensure_ascii=False)]=validate_price(price)
    store.set_setting('codex_usage',value)
    return value


def timestamp(value):
    try:
        return datetime.fromisoformat(value.replace('Z','+00:00')).timestamp()
    except (ValueError,TypeError,AttributeError,OverflowError):
        return None


def counters(value):
    if not isinstance(value,dict):
        return None
    names=('input_tokens','cached_input_tokens','output_tokens','cache_write_input_tokens')
    values=[value.get(name,0 if name in ('cached_input_tokens','cache_write_input_tokens') else None) for name in names]
    if any(not isinstance(v,int) or isinstance(v,bool) or v<0 for v in values) or values[1]>values[0]:
        return None
    return dict(zip(names,values))


def event_record(event,state,path,offset, *, include_cumulative=False):
    kind=event.get('type');payload=event.get('payload') or {}
    if not isinstance(payload,dict):
        return None
    if kind=='session_meta':
        state['cli_version']=payload.get('cli_version') if isinstance(payload.get('cli_version'),str) else 'unknown'
        state['thread']=payload.get('id') or payload.get('thread_id') or state.get('thread')
        state['provider']=payload.get('model_provider') or state.get('provider','unknown')
        state['fork']=bool(payload.get('forked_from_id') or payload.get('forked_from'))
        state['fork_cutoff']=timestamp(event.get('timestamp'))
        for field in ('thread','provider'):
            if not isinstance(state.get(field),str):state[field]='unknown'
        return None
    if kind=='turn_context' or (kind=='event_msg' and payload.get('type')=='thread_settings_applied'):
        state['model']=payload.get('model') or state.get('model','unknown')
        state['provider']=payload.get('model_provider') or state.get('provider','unknown')
        for field in ('model','provider'):
            if not isinstance(state.get(field),str):state[field]='unknown'
        return None
    direct=kind=='token_usage_record'
    cumulative=kind=='event_msg' and payload.get('type')=='token_count'
    if not direct and not cumulative:
        return None
    at=timestamp(event.get('timestamp'))
    if at is None:
        state['invalid']=state.get('invalid',0)+1
        return None
    if state.get('fork') and state.get('fork_cutoff') and at <= state['fork_cutoff']:
        state['fork_replayed']=state.get('fork_replayed',0)+1
        return None
    quality='reported'
    if direct:
        state['direct']=True
        raw_count=payload.get('usage')
        count=counters(raw_count)
    else:
        if state.get('direct') and not include_cumulative:
            state['skipped_cumulative']=state.get('skipped_cumulative',0)+1
            return None
        info=payload.get('info') or {}
        raw_count=info.get('total_token_usage')
        count=counters(raw_count)
        if count is None:
            state['invalid']=state.get('invalid',0)+1
            return None
        previous=state.get('cumulative')
        state['cumulative']=count
        if previous:
            if any(count[k]<previous[k] for k in count):
                state['resets']=state.get('resets',0)+1
                return None
            count={k:count[k]-previous[k] for k in count}
            if count['cached_input_tokens']>count['input_tokens']:
                state['invalid']=state.get('invalid',0)+1
                return None
        elif state.get('fork'):
            state['fork_baselines']=state.get('fork_baselines',0)+1
            return None
        quality='cumulative_delta'
    if count is None:
        state['invalid']=state.get('invalid',0)+1
        return None
    if not any(count.values()):
        return None
    provider=payload.get('model_provider') or state.get('provider','unknown')
    model=payload.get('model') or state.get('model','unknown')
    provider=provider if isinstance(provider,str) else 'unknown'
    model=model if isinstance(model,str) else 'unknown'
    response=payload.get('response_id')
    if response and direct:
        identity=['response',provider,response]
    elif state.get('thread'):
        # Copied rollouts and replay prefixes retain their event timestamp/counters.
        # A cumulative checkpoint, rather than its delta, distinguishes two
        # equally-sized responses recorded within one timestamp resolution.
        identity=['event',payload.get('thread_id') or state['thread'],event.get('timestamp'),
                  count if direct else state['cumulative'],model]
        # A monotonic cumulative chain has exact reported totals even when the
        # host does not assign a response ID. Forks/resets retain separate gaps.
        quality='identity_inferred' if direct else 'cumulative_delta'
    else:
        identity=['file',path,offset]
        quality='identity_unknown'
    key=hashlib.sha256(json.dumps(identity,sort_keys=True).encode()).hexdigest()
    return key,at,'direct' if direct else 'cumulative',dict(occurred_at=at,model=model,provider=provider,
        prompt=count['input_tokens'],cached_input=count['cached_input_tokens'] if 'cached_input_tokens' in raw_count else None,completion=count['output_tokens'],
        cache_write_input=count['cache_write_input_tokens'],quality=quality,
        response_id=response if isinstance(response,str) else None,
        session_started_at=state.get('fork_cutoff'),identity_version=PARSER_VERSION)


def _drop_sources(db,path,kind=None):
    if kind:
        db.execute('DELETE FROM codex_usage_sources WHERE path=? AND event_key IN (SELECT event_key FROM codex_usage_records WHERE kind=?)',(path,kind))
    else:
        db.execute('DELETE FROM codex_usage_sources WHERE path=?',(path,))
    db.execute('DELETE FROM codex_usage_records WHERE NOT EXISTS (SELECT 1 FROM codex_usage_sources s WHERE s.event_key=codex_usage_records.event_key)')


def scan(store, config):
    folder=Path(config['directory'])/'sessions'
    if not folder.is_dir():
        return {'unavailable':True,'pending_files':0,'unreadable_files':0}
    status=Counter(files=0,bytes_read=0,consumed_bytes=0,unreadable_files=0,pending_files=0)
    deadline=time.monotonic()+1.0
    with store.connect() as db:
        schema(db)
    try:
        # A file rotated away between listing and sorting must not invalidate the whole scan.
        def newest(file):
            try:
                return file.stat().st_mtime
            except OSError:
                return 0
        files=sorted(folder.rglob('*.jsonl'),key=newest,reverse=True)
    except OSError:
        return {'unavailable':True,'pending_files':0,'unreadable_files':1}
    enabled=settings(store)['enabled']
    for file in files:
        if not enabled:
            return {**status,'stopped':True}
        name=str(file.resolve())
        try:
            stat=file.stat();identity=f'{stat.st_dev}:{stat.st_ino}'
            with store.connect() as db:
                row=db.execute('SELECT * FROM codex_usage_files WHERE path=?',(name,)).fetchone()
            saved_state=json.loads(row['state']) if row else {}
            reset=not row or saved_state.get('parser_version')!=PARSER_VERSION or row['identity']!=identity or stat.st_size<row['offset'] or (stat.st_size==row['offset'] and stat.st_mtime_ns!=row['modified'])
            offset=0 if reset else row['offset']
            state={} if reset else saved_state
            state['parser_version']=PARSER_VERSION
            if row and not reset and offset==stat.st_size:
                continue
            if status['bytes_read']>=SCAN_BYTES or time.monotonic()>=deadline:
                status['pending_files']+=1
                continue
            records=[];had_direct=state.get('direct',False)
            initial_offset=offset
            with file.open('rb') as stream:
                stream.seek(offset)
                while status['bytes_read']<SCAN_BYTES and time.monotonic()<deadline:
                    beginning=stream.tell();line=stream.readline(256*1024)
                    if not line:
                        break
                    status['bytes_read']+=len(line)
                    if not line.endswith(b'\n'):
                        if len(line)==256*1024:
                            if not state.get('skip_line'):state['oversized_lines']=state.get('oversized_lines',0)+1
                            state['skip_line']=True;offset=stream.tell()
                            continue
                        break  # An actively written record is retried, never consumed half-way.
                    offset=stream.tell()
                    if state.pop('skip_line',False):
                        continue
                    head=line[:1024]
                    if not any(tag in head for tag in (b'token_usage_record',b'token_count',b'session_meta',b'turn_context',b'thread_settings_applied')):
                        continue
                    try:
                        event=json.loads(line.decode('utf-8-sig'))
                        record=event_record(event,state,name,beginning)
                        if record:records.append(record)
                    except (ValueError,TypeError,AttributeError):
                        state['invalid']=state.get('invalid',0)+1
            # Parsing happens outside the write transaction; control operations remain responsive.
            with store.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                latest_config=json.loads(db.execute("SELECT value FROM settings WHERE key='codex_usage'").fetchone()[0])
                if not latest_config['enabled'] or latest_config['directory']!=config['directory']:
                    return {**status,'stopped':True}
                latest=db.execute('SELECT offset,identity FROM codex_usage_files WHERE path=?',(name,)).fetchone()
                if (latest is None)!=(row is None) or (latest and (latest['offset']!=row['offset'] or latest['identity']!=row['identity'])):
                    status['pending_files']+=1
                    continue
                if reset:_drop_sources(db,name)
                if state.get('direct') and not had_direct:
                    state['skipped_cumulative']=state.get('skipped_cumulative',0)+db.execute('SELECT count(*) FROM codex_usage_sources s JOIN codex_usage_records r ON s.event_key=r.event_key WHERE s.path=? AND r.kind=?',(name,'cumulative')).fetchone()[0]
                    _drop_sources(db,name,'cumulative')
                for key,at,kind,payload in records:
                    if state.get('direct') and kind=='cumulative':
                        state['skipped_cumulative']=state.get('skipped_cumulative',0)+1
                        continue
                    existing=db.execute('SELECT payload FROM codex_usage_records WHERE event_key=?',(key,)).fetchone()
                    if existing:
                        previous=json.loads(existing[0])
                        if any(previous.get(field)!=payload.get(field) for field in ('model','provider','prompt','completion','cached_input')):
                            state['conflicting']=state.get('conflicting',0)+1
                    db.execute('INSERT OR IGNORE INTO codex_usage_records VALUES (?,?,?,?)',(key,at,kind,json.dumps(payload)))
                    db.execute('INSERT OR IGNORE INTO codex_usage_sources VALUES (?,?)',(name,key))
                db.execute('INSERT OR REPLACE INTO codex_usage_files VALUES (?,?,?,?,?,?)',
                    (name,identity,stat.st_size,stat.st_mtime_ns,offset,json.dumps(state)))
            status['files']+=1
            status['consumed_bytes']+=offset-initial_offset
            if offset<stat.st_size:status['pending_files']+=1
        except OSError:
            status['unreadable_files']+=1
    return dict(status)


def today(store,now=None):
    config=settings(store)
    if not config['enabled']:
        return {'enabled':False,'settings':config}
    scanned=scan(store,config)
    current,start,end=daily_window(now)
    totals=Counter(prompt=0,completion=0,cached_input=0);groups={};coverage=Counter();versions=set()
    with store.connect() as db:
        schema(db)
        for row in db.execute('SELECT payload FROM codex_usage_records WHERE occurred_at>=? AND occurred_at<?',(start,end)):
            event=json.loads(row[0]);key=json.dumps([event['provider'],event['model']],ensure_ascii=False)
            group=groups.setdefault(key,dict(provider=event['provider'],model=event['model'],requests=0,prompt=0,completion=0,cached_input=0,cost=None,unpriced=0))
            group['requests']+=1
            for field in ('prompt','completion','cached_input'):
                group[field]+=event[field] or 0;totals[field]+=event[field] or 0
            if event['cached_input'] is None:coverage['cache_unknown']+=1
            coverage[event['quality']]+=1
            price=config['prices'].get(key)
            if price and not event['cache_write_input']:
                bounds=estimate(event,price)
                before=group['cost'] or {'low':'0','high':'0'}
                group['cost']=amount((Decimal(before['low'])+bounds[0],Decimal(before['high'])+bounds[1]))
            else:group['unpriced']+=1
        for row in db.execute('SELECT state FROM codex_usage_files'):
            state=json.loads(row[0])
            versions.add(state.get('cli_version','unknown'))
            if state.get('parser_version')!=PARSER_VERSION:coverage['legacy_parser_pending']+=1
            for field in ('invalid','resets','fork_baselines','fork_replayed','skipped_cumulative','conflicting','oversized_lines'):
                coverage[field]+=state.get(field,0)
    totals['total']=totals['prompt']+totals['completion']
    return dict(enabled=True,settings=config,date=current.date().isoformat(),timezone=current.tzname(),
                generated_at=time.time(),scan=scanned,tokens=dict(totals),coverage=dict(coverage),groups=list(groups.values()),versions=sorted(versions))
