import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'engine'))
from codexrelay.state import Store
from codexrelay import codex_usage
from codexrelay.usage import requests_today

NOW=datetime(2026,10,13,12,tzinfo=timezone(timedelta(hours=8)))
def meta(thread='thread',**extra):
    return {'type':'session_meta','timestamp':'2026-10-12T12:00:00Z','payload':{'id':thread,'model_provider':'custom',**extra}}
def context(model='model-a'):
    return {'type':'turn_context','payload':{'model':model}}
def direct(identifier='response-1',stamp='2026-10-13T02:00:00Z',**usage):
    return {'timestamp':stamp,'type':'token_usage_record','payload':{'response_id':identifier,'usage':{'input_tokens':100,'cached_input_tokens':20,'output_tokens':10,'reasoning_output_tokens':5,**usage}}}
def cumulative(total,stamp='2026-10-13T02:00:00Z'):
    return {'timestamp':stamp,'type':'event_msg','payload':{'type':'token_count','info':{'total_token_usage':{'input_tokens':total,'cached_input_tokens':0,'output_tokens':total//10}}}}


class UsageSourcesTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='log-usage-',dir=ROOT/'.tmp');self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.store=Store(self.root/'state');self.codex=self.root/'codex'
        self.folder=self.codex/'sessions/2025/01/01';self.folder.mkdir(parents=True)
        self.file=self.folder/'rollout-fixture.jsonl'

    def write(self,*events,file=None):
        (file or self.file).write_text(''.join(json.dumps(event)+'\n' for event in events),encoding='utf-8')

    def enable(self):codex_usage.configure(self.store,True,str(self.codex))
    def today(self):return codex_usage.today(self.store,NOW)

    def test_disabled_never_scans_or_reads_logs(self):
        with patch.object(Path,'rglob',side_effect=AssertionError('must not scan')):
            self.assertFalse(self.today()['enabled'])

    def test_direct_dedup_model_history_and_old_directory(self):
        self.write(meta(),context(),direct(),direct(),context('model-b'),direct('response-2'))
        self.enable();day=self.today()
        self.assertEqual(day['tokens']['total'],220)
        self.assertEqual({g['model'] for g in day['groups']},{'model-a','model-b'})
        (self.codex/'config.toml').write_text('model="unrelated-current-model"')
        self.assertEqual(self.today()['tokens'],day['tokens'])
        self.assertEqual(self.today()['scan']['bytes_read'],0)

    def test_cumulative_delta_duplicate_and_reset(self):
        self.write(meta(),context(),cumulative(100),cumulative(100),cumulative(200,'2026-10-13T02:01:00Z'),cumulative(10),cumulative(30,'2026-10-13T02:02:00Z'))
        self.enable();day=self.today()
        self.assertEqual(day['tokens']['total'],242)
        self.assertEqual(day['coverage']['resets'],1)

    def test_mixed_formats_do_not_add_two_accounting_chains(self):
        self.write(meta(),context(),cumulative(100),direct())
        self.enable();day=self.today()
        self.assertEqual(day['tokens']['total'],110)
        self.assertEqual(day['coverage']['skipped_cumulative'],1)

    def test_half_record_append_and_truncation(self):
        self.write(meta(),context())
        encoded=json.dumps(direct())
        with self.file.open('a') as stream:stream.write(encoded[:40])
        self.enable();self.assertEqual(self.today()['tokens']['total'],0)
        with self.file.open('a') as stream:stream.write(encoded[40:]+'\n')
        self.assertEqual(self.today()['tokens']['total'],110)
        self.write(meta(),context(),direct('replacement',input_tokens=20,cached_input_tokens=0))
        self.assertEqual(self.today()['tokens']['total'],30)

    def test_copy_and_fork_replay(self):
        self.write(meta(),context(),direct())
        other=self.folder/'rollout-copy.jsonl';other.write_bytes(self.file.read_bytes())
        fork=self.folder/'rollout-fork.jsonl'
        self.write(meta('child',forked_from_id='thread'),context(),direct(),direct('new','2026-10-13T03:00:00Z'),file=fork)
        self.enable();self.assertEqual(self.today()['tokens']['total'],220)

    def test_fork_without_ids_skips_history_before_fork(self):
        header=meta('child',forked_from_id='thread');header['timestamp']='2026-10-13T02:30:00Z'
        self.write(header,context(),direct(None),direct(None,'2026-10-13T03:00:00Z'))
        self.enable();day=self.today()
        self.assertEqual(day['tokens']['total'],110)
        self.assertEqual(day['coverage']['fork_replayed'],1)

    def test_cost_cache_reasoning_and_unknown_cache(self):
        self.write(meta(),context(),direct())
        self.enable();codex_usage.save_price(self.store,'custom','model-a',{'input':'10','cached_input':'2','output':'20'})
        self.assertEqual(self.today()['groups'][0]['cost'],{'low':'0.00104','high':'0.00104'})
        event=direct('unknown-cache');del event['payload']['usage']['cached_input_tokens']
        self.write(meta(),context(),event)
        day=self.today();self.assertEqual(day['tokens']['total'],110)
        self.assertEqual(day['coverage']['cache_unknown'],1)
        self.assertEqual(day['groups'][0]['cost'],{'low':'0.0004','high':'0.0012'})

    def test_local_date_boundaries(self):
        self.write(meta(),context(),direct('before','2026-10-12T15:59:59Z'),direct('start','2026-10-12T16:00:00Z'),direct('end','2026-10-13T16:00:00Z'))
        self.enable();self.assertEqual(self.today()['tokens']['total'],110)

    def test_cache_contains_no_body_and_clear_preserves_source(self):
        secret='PRIVATE_MESSAGE_MUST_NOT_BE_CACHED'
        self.write(meta(),context(),{'type':'response_item','payload':{'content':secret}},direct())
        self.enable();day=self.today()
        with self.store.connect() as db:
            for table in ('codex_usage_files','codex_usage_records','codex_usage_sources'):
                self.assertNotIn(secret,str([tuple(row) for row in db.execute('SELECT * FROM '+table)]))
        codex_usage.configure(self.store,False)
        codex_usage.clear(self.store)
        self.assertIn(secret,self.file.read_text())
        with self.store.connect() as db:self.assertEqual(db.execute('SELECT count(*) FROM codex_usage_records').fetchone()[0],0)
        self.assertFalse(self.today()['enabled'])

    def test_chunk_budget_and_missing_directory(self):
        self.enable();self.write(meta(),context(),direct())
        with patch.object(codex_usage,'SCAN_BYTES',1):
            self.assertEqual(self.today()['scan']['pending_files'],1)
        self.assertEqual(self.today()['tokens']['total'],110)
        codex_usage.configure(self.store,True,str(self.root))
        self.assertTrue(self.today()['scan']['unavailable'])

    def test_request_event_persistence_is_idempotent_and_uses_response_day(self):
        with self.store.connect() as db:
            db.execute('INSERT INTO tasks VALUES (?,?,0)',('job',json.dumps({'id':'job','status':'completed','finished_at':NOW.timestamp()+86400,'usage_price':None})))
        event={'sequence':1,'occurred_at':NOW.timestamp(),'prompt':100,'completion':10,'cached_input':20,'cache_write_input':None,'available':True}
        for _ in range(2):self.store.update_task('job',telemetry={'usage_events':[event]})
        day=requests_today(self.store,NOW)
        self.assertEqual(day['counts']['responses'],1)
        self.assertEqual(day['tokens']['total'],110)

    def test_oversized_body_is_skipped_without_losing_following_usage(self):
        self.write(meta(),context(),{'type':'response_item','payload':{'content':'x'*1_000_000}},direct())
        self.enable();day=self.today()
        self.assertEqual(day['tokens']['total'],110)
        self.assertEqual(day['coverage']['oversized_lines'],1)

    def test_nonfinite_response_time_does_not_lose_task_settlement(self):
        with self.store.connect() as db:
            db.execute('INSERT INTO tasks VALUES (?,?,1)', ('job', json.dumps({'id':'job','status':'running'})))
        events = [{'sequence':i, 'occurred_at':stamp} for i,stamp in enumerate(
            (float('nan'), float('inf'), float('-inf')), 1)]
        self.store.update_task('job', status='completed', finished_at=NOW.timestamp(), telemetry={'usage_events':events})
        self.assertEqual(self.store.task('job')['status'], 'completed')
        with self.store.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM usage_events').fetchone()[0], 0)

    def test_retroactive_cache_counter_does_not_produce_negative_cost(self):
        first=cumulative(100);second=cumulative(100)
        second['payload']['info']['total_token_usage']['cached_input_tokens']=20
        self.write(meta(),context(),first,second)
        self.enable();day=self.today()
        self.assertEqual(day['tokens']['total'],110)
        self.assertEqual(day['coverage']['invalid'],1)


if __name__=='__main__':unittest.main()
