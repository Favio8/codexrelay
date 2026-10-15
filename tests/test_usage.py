"""Usage contracts: real SQLite, independent price arithmetic, no provider requests."""
import json
import sys
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'engine'))
from codexrelay.state import Store
from codexrelay.service import Service
from codexrelay.usage import usage_today, daily_window, validate_price, estimate
from test_control import MemoryVault


class UsageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='usage-', dir=ROOT / '.tmp')
        self.addCleanup(self.temp.cleanup)
        self.workspace = Path(self.temp.name)
        self.store = Store(self.workspace / 'state')
        self.service = Service(self.store, MemoryVault())
        result = self.service.save_provider(name='Example', kind='custom', base_url='http://localhost:8080',
            flash_model='flash', pro_model='pro', reasoning_effort='provider-default')
        self.provider = self.store.provider(result['id'])
        self.store.set_setting('enabled', True)

    def price(self, input='2', output='3', model='flash', cached_input=None, reference=False):
        params = {'price':dict(input=input, output=output, cached_input=cached_input, currency='CNY', label='Reference')}
        if reference:
            params['reference'] = True
        else:
            params.update(provider_id=self.provider['id'], provider_revision=self.provider['revision'], model=model)
        return self.service.dispatch('save_usage_price', params)

    def task(self, status='completed', prompt=1_000_000, completion=100_000, **changes):
        task = self.store.admit('Example task', self.workspace, 'readonly', changes.pop('slot', 'flash'))
        tokens = dict(prompt=prompt, completion=completion, total=prompt+completion, available=True,
            responses_with_usage=1, responses_without_usage=0, interrupted_request=False)
        updates = dict(status=status, finished_at=time.time(), telemetry={'tokens':tokens}, result={'tokens':tokens})
        updates.update(changes)
        return self.store.update_task(task['id'], **updates)

    def today(self):
        return self.service.dispatch('usage_today', {})

    def test_price_example_signed_comparison_and_snapshot_survives_changes(self):
        self.price()
        self.price('10', '20', reference=True)
        task = self.task()
        day = self.today()
        self.assertEqual(day['cost'], {'low':'2.3','high':'2.3'})
        self.assertEqual(day['comparison'], {'low':'9.7','high':'9.7'})
        self.price('100', '200')
        self.assertEqual(self.today()['cost'], day['cost'])
        self.price('0', '0', reference=True)
        self.assertEqual(self.today()['comparison'], {'low':'-2.3','high':'-2.3'})
        self.store.delete_provider(self.provider['id'])
        self.assertEqual(self.today()['cost'], day['cost'])
        self.assertEqual(self.store.task(task['id'])['usage_price']['source'], 'task_start')

    def test_all_statuses_and_no_double_count_with_result_or_budget(self):
        for status in ('completed','failed','cancelled','interrupted'):
            self.task(status, prompt=20, completion=10)
        active = self.store.admit('Running', self.workspace, 'readonly', 'flash')
        self.store.update_task(active['id'], telemetry={'budget':{'metered_tokens':999999},'tokens':{'prompt':500,'completion':200}})
        day = self.today()
        self.assertEqual(day['tokens']['total'],120)
        self.assertEqual(day['counts']['settled'],4)
        self.assertEqual(day['counts']['unsettled'],1)
        self.assertEqual(day['coverage']['partial'],1)
        self.assertEqual(self.today()['tokens'], day['tokens'])

    def test_missing_explicit_unknown_legacy_and_partial(self):
        self.task(telemetry={'tokens':{'available':False,'prompt':None,'completion':None}})
        self.task(telemetry=None, result={'tokens':{'prompt':20,'completion':10}})
        self.task(telemetry={'tokens':{'prompt':40,'completion':10,'responses_without_usage':1}})
        self.task(telemetry={'tokens':{'prompt':-10,'completion':5}})
        day = self.today()
        self.assertEqual(day['tokens']['total'],80)
        self.assertEqual(day['coverage']['missing'],2)
        self.assertEqual(day['coverage']['legacy'],1)
        self.assertEqual(day['coverage']['partial'],1)

    def test_missing_is_not_zero_but_explicit_free_price_is_valid(self):
        self.task(telemetry={'tokens':{'available':False}}, result=None)
        self.assertIsNone(self.today()['tokens'])
        self.assertIsNone(self.today()['cost'])
        self.price('0','0')
        self.task(prompt=100,completion=20)
        day=self.today()
        self.assertEqual(day['cost'],{'low':'0','high':'0'})
        self.assertEqual(day['priced_tasks'],1)

    def test_cache_interval_and_known_cache(self):
        self.price(input='10',output='20',cached_input='2')
        self.task()
        self.assertEqual(self.today()['cost'], {'low':'4','high':'12'})
        self.price(input='1',output='1',cached_input='3',reference=True)
        self.assertEqual(self.today()['comparison'], {'low':'-10.9','high':'-0.9'})
        bounds=estimate({'prompt':1_000_000,'completion':100_000,'cached_input':250_000},
                        validate_price({'input':'10','output':'20','cached_input':'2'}))
        self.assertEqual(tuple(map(str,bounds)), ('10','10'))

    def test_price_validation(self):
        for value in ('',None,'-1','NaN','Infinity',True,'0.000000001'):
            with self.subTest(value=value),self.assertRaises(ValueError):
                validate_price({'input':value,'output':'1'})
        with self.assertRaises(ValueError):
            validate_price({'currency':'USD','input':'1','output':'1'})

    def test_model_and_provider_revision_are_exact(self):
        self.price()
        self.price('10','20',model='pro')
        self.task(slot='pro')
        self.assertEqual(self.today()['cost'],{'low':'12','high':'12'})
        params={**self.provider,'name':'Edited','base_url':'https://changed.example/v1'}
        self.service.save_provider(**params)
        task=self.task()
        self.assertIsNone(task['usage_price'])
        self.assertEqual(self.today()['unpriced_tasks'],1)
        self.assertEqual(self.today()['priced_tasks'],1)

    def test_backfill_is_explicit_scoped_and_idempotent(self):
        task=self.task()
        self.price()
        self.assertIsNone(self.today()['cost'])
        params=dict(date=self.today()['date'],provider_id=self.provider['id'],provider_revision=self.provider['revision'],model='flash')
        self.assertEqual(self.service.dispatch('backfill_usage_price',params),{'updated':1})
        self.price('10','20')
        self.assertEqual(self.service.dispatch('backfill_usage_price',params),{'updated':0})
        self.assertEqual(self.today()['cost'],{'low':'2.3','high':'2.3'})
        self.assertEqual(self.store.task(task['id'])['usage_price']['source'],'backfill')
        with self.assertRaisesRegex(ValueError,'日期'):
            self.service.dispatch('backfill_usage_price',{**params,'date':'2000-01-01'})

    def test_day_boundary_uses_finish_not_creation(self):
        now=datetime(2026,10,13,12,tzinfo=timezone(timedelta(hours=8)))
        _,start,end=daily_window(now)
        self.task(created_at=start-86400,finished_at=start)
        self.task(finished_at=start-0.001)
        self.task(finished_at=end)
        self.task(finished_at=None)
        day=usage_today(self.store,now)
        self.assertEqual(day['counts']['settled'],1)
        self.assertEqual(day['coverage']['undated_records'],1)
        self.assertEqual(day['date'],'2026-10-13')
        utc=usage_today(self.store,now.astimezone(timezone.utc))
        self.assertEqual(utc['counts']['settled'],1)
        self.assertNotEqual(utc['recent'][0]['id'],day['recent'][0]['id'])

    def test_requests_today_isolates_broken_and_non_numeric_records(self):
        self.price()
        task=self.task(prompt=100,completion=50)
        self.store.update_task(task['id'],telemetry={'tokens':{'prompt':100,'completion':50,'total':150,'available':True,
            'responses_with_usage':1,'responses_without_usage':0,'interrupted_request':False},'usage_events':[
            {'sequence':1,'occurred_at':time.time(),'available':True,'prompt':100,'completion':50,'cached_input':None,'cache_write_input':None},
            {'sequence':2,'occurred_at':time.time(),'available':True,'prompt':'oops','completion':50,'cached_input':None,'cache_write_input':None}]})
        with self.store.connect() as db:
            db.execute('INSERT OR REPLACE INTO usage_events VALUES (?,?,?,?)',(task['id'],3,time.time(),'{'))
            db.execute('INSERT OR REPLACE INTO usage_events VALUES (?,?,?,?)',(task['id'],4,'not-a-time','{}'))
        day=self.service.dispatch('usage_requests_today',{})
        self.assertEqual(day['counts']['responses'],1)
        self.assertEqual(day['tokens']['total'],150)
        self.assertEqual(day['coverage']['invalid_records'],2)
        self.assertEqual(day['counts']['cache_unknown'],1)

    def test_malformed_event_does_not_roll_back_the_task_settlement(self):
        task=self.task(prompt=10,completion=5)
        self.store.update_task(task['id'],status='cancelled',finished_at=time.time(),
            telemetry={'usage_events':[{'sequence':'one','occurred_at':time.time()},{'sequence':2,'occurred_at':'soon'}]})
        stored=self.store.task(task['id'])
        self.assertEqual(stored['status'],'cancelled')
        with self.store.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM usage_events WHERE job_id=?',(task['id'],)).fetchone()[0],0)

    def test_cache_write_is_unpriced_instead_of_swallowed(self):
        self.price()
        task=self.cache_write_task()
        day=self.today()
        self.assertEqual(day['priced_tasks'],0)
        self.assertEqual(day['unpriced_tasks'],1)
        self.assertIsNone(day['cost'])
        self.assertEqual(day['coverage']['cache_write_unpriced'],1)
        requests=self.service.dispatch('usage_requests_today',{})
        self.assertEqual(requests['counts']['unpriced'],1)
        self.assertIsNone(requests['cost'])
        self.assertEqual(requests['coverage']['cache_write_unpriced'],1)

    def test_cache_write_is_priced_once_a_rate_exists(self):
        self.service.dispatch('save_usage_price',{'price':dict(input='2',output='3',cached_input=None,cache_write='5',currency='CNY',label=''),
            'provider_id':self.provider['id'],'provider_revision':self.provider['revision'],'model':'flash'})
        self.cache_write_task()
        day=self.today()
        self.assertEqual(day['priced_tasks'],1)
        self.assertEqual(day['cost'],{'low':'2.02048','high':'2.02048'})
        requests=self.service.dispatch('usage_requests_today',{})
        self.assertEqual(requests['counts']['priced'],1)
        self.assertEqual(requests['cost'],{'low':'2.02048','high':'2.02048'})

    def test_amounts_stay_in_fixed_point_notation(self):
        self.service.dispatch('save_usage_price',{'price':dict(input='0.00000001',output='0',cached_input=None,currency='CNY',label=''),
            'provider_id':self.provider['id'],'provider_revision':self.provider['revision'],'model':'flash'})
        self.cache_write_task(prompt=1,completion=0,written=0)
        self.assertEqual(self.today()['cost'],{'low':'0.00000000000001','high':'0.00000000000001'})

    def test_missing_output_price_is_rejected_before_persistence(self):
        for output in (None, ''):
            with self.subTest(output=output), self.assertRaises(ValueError):
                self.service.dispatch('save_usage_price', {'price':dict(input='2', output=output),
                    'provider_id':self.provider['id'], 'provider_revision':self.provider['revision'], 'model':'flash'})
        self.assertEqual(self.service.dispatch('get_usage_prices', {}), {'models':{}, 'reference':None})

    def cache_write_task(self,prompt=1_000_000,completion=0,written=4096):
        tokens=dict(prompt=prompt,completion=completion,total=prompt+completion,available=True,
                    responses_with_usage=1,responses_without_usage=0,interrupted_request=False,cached_input=0)
        task=self.store.admit('Cache write',self.workspace,'readonly','flash')
        return self.store.update_task(task['id'],status='completed',finished_at=time.time(),
            telemetry={'tokens':tokens,'usage_events':[{'sequence':1,'occurred_at':time.time(),'available':True,
                'prompt':prompt,'completion':completion,'cached_input':0,'cache_write_input':written}]})

    def test_statistics_read_all_tasks_and_isolate_bad_json(self):
        base=self.task(prompt=2,completion=1)
        with self.store.connect() as db:
            db.executemany('INSERT INTO tasks VALUES (?,?,0)',[(f't{i}',json.dumps({**base,'id':f't{i}'})) for i in range(10000)])
            db.execute("INSERT INTO tasks VALUES ('broken','not-json',0)")
        started=time.perf_counter()
        day=self.today()
        elapsed=time.perf_counter()-started
        self.assertEqual(day['tokens']['total'],30003)
        self.assertEqual(day['counts']['settled'],10001)
        self.assertEqual(day['coverage']['invalid_records'],1)
        self.assertEqual(len(day['recent']),50)
        print(f'usage performance: 10,001 tasks, {elapsed:.3f}s')
        self.assertLess(elapsed,1.0)

    def test_revision_changes_only_for_settlement_and_prices(self):
        before=self.store.snapshot().get('usage_revision',0)
        task=self.store.admit('Run',self.workspace,'readonly','flash')
        admitted=self.store.snapshot()['usage_revision']
        self.assertGreater(admitted,before)
        self.store.update_task(task['id'],status='running',telemetry={'tokens':{}})
        self.assertEqual(self.store.snapshot()['usage_revision'],admitted)
        self.store.update_task(task['id'],status='completed',finished_at=time.time())
        self.assertGreater(self.store.snapshot()['usage_revision'],admitted)
        self.price()
        snapshot=self.store.snapshot()
        self.assertNotIn('usage_prices',snapshot)
        self.assertEqual(snapshot['usage_source'],str(self.store.directory.resolve()))

    def test_stale_process_settlement_invalidates_usage(self):
        self.store.admit('Crashed',self.workspace,'readonly','flash')
        before=self.store.snapshot()['usage_revision']
        with patch('codexrelay.state.process_identity',return_value=None):
            after=self.store.snapshot()['usage_revision']
        self.assertGreater(after,before)
        self.assertEqual(self.today()['counts']['interrupted'],1)


if __name__=='__main__':
    unittest.main()
