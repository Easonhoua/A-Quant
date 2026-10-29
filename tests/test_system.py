import unittest, tempfile, copy
from pathlib import Path
from datetime import datetime,date
from zoneinfo import ZoneInfo
from fastapi.testclient import TestClient
from backend.data import demo
from backend.engine import allowed, daily, backtest, freshness, valid_bars
from backend.store import Store
import backend.app as module
from unittest.mock import patch

class SystemTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.store=Store(Path(self.temp.name)/'test.db')
        self.old=module.store; module.store=self.store; self.client=TestClient(module.app)
        self.snapshot=demo()
    def tearDown(self): module.store=self.old; self.temp.cleanup()
    def trade(self,side='BUY',quantity=100,code='600001',key='unique-request-1',mode='demo'):
        return {'code':code,'side':side,'quantity':quantity,'price':10.0,'trade_date':'2025-10-01','mode':mode,'request_id':key}
    def test_board_filter(self):
        for c in ['600001','601001','603001','605001','000001','001001','002001','003001','300001','301001']: self.assertTrue(allowed(c,'正常'))
        for c,n in [('688001','正常'),('920001','正常'),('830001','正常'),('600001','*ST甲'),('002001','退市甲'),('900001','正常')]: self.assertFalse(allowed(c,n))
    def test_buy_partial_sell_close(self):
        self.store.record(self.trade(quantity=300))
        self.store.record(self.trade('SELL',100,key='unique-request-2'))
        self.assertEqual(self.store.positions()[0]['quantity'],200)
        self.store.record(self.trade('SELL',200,key='unique-request-3'))
        self.assertEqual(self.store.positions(),[])
    def test_idempotence(self):
        t=self.trade(); self.store.record(t); self.store.record(t)
        self.assertEqual(len(self.store.ledger('demo')),1)
        with self.assertRaises(ValueError):self.store.record({**t,'quantity':200})
    def test_oversell_rollback(self):
        self.store.record(self.trade())
        with self.assertRaises(ValueError):self.store.record(self.trade('SELL',200,key='unique-request-2'))
        self.assertEqual(self.store.positions()[0]['quantity'],100)
    def test_mode_isolation(self):
        self.store.record(self.trade()); self.assertEqual(self.store.positions('live'),[])
    def test_weighted_cost(self):
        self.store.record(self.trade());self.store.record({**self.trade(key='unique-request-2'),'price':20})
        self.assertEqual(self.store.positions()[0]['avg_cost'],15)
    def test_no_silent_holding_mutation(self):
        self.store.record(self.trade()); before=self.store.ledger('demo')
        r=self.client.get('/api/daily').json()
        self.assertEqual(r['sequence'],['持仓逐一分析','组合风险','新选股'])
        self.client.post('/api/backtest',json={'code':'600001'})
        self.assertEqual(self.store.ledger('demo'),before)
        self.assertEqual(len(r['holdings']),1)
    def test_unknown_holding_blocks(self):
        r=daily(self.snapshot,[{'code':'999999','quantity':100,'avg_cost':10}])
        self.assertIsNone(r['risk']['exposure']);self.assertTrue(all(c['suggested_quantity']==0 for c in r['candidates']))
    def test_demo_is_never_tradeable(self):
        r=daily(self.snapshot,[]);self.assertTrue(r['candidates']);self.assertTrue(all(c['suggested_quantity']==0 for c in r['candidates']))
    def test_liquidity_suspension(self):
        r=daily(self.snapshot,[]);excluded={e['code']:e['reason'] for e in r['excluded']}
        self.assertIn('600002',excluded);self.assertIn('600003',excluded);self.assertIn('688001',excluded)
    def test_invalid_bars(self):
        b=copy.deepcopy(self.snapshot['stocks'][0]['bars']);b[-1]['close']=float('nan');self.assertFalse(valid_bars(b))
        b=copy.deepcopy(self.snapshot['stocks'][0]['bars']);b[-1]['date']=b[-2]['date'];self.assertFalse(valid_bars(b))
    def test_dynamic_old_cache(self):
        s={'mode':'live','as_of':'2026-10-26','expected_session':'2026-10-26','calendar_verified':True,'complete':True,'calendar':['2026-10-26','2026-10-27','2026-10-28','2026-10-29']}
        now=datetime(2026,10,28,18,tzinfo=ZoneInfo('Asia/Shanghai'))
        self.assertTrue(any('不一致' in w for w in freshness(s,now)))
        s['as_of']='2026-10-28';s['stocks']=[{'bars':[{'date':'2026-10-28'}]}];self.assertEqual(freshness(s,now),[])
    def test_preclose(self):
        s={'mode':'live','as_of':'2026-10-28','calendar_verified':True,'complete':True,'calendar':['2026-10-27','2026-10-28','2026-10-29']}
        self.assertTrue(freshness(s,datetime(2026,10,28,14,tzinfo=ZoneInfo('Asia/Shanghai'))))
    def test_backtest_no_lookahead(self):
        s=self.snapshot['stocks'][0];r=backtest(s)
        if len(r['trades'])==0: self.fail('fixture must exercise fills')
        for t in r['trades']:
            index=next(i for i,b in enumerate(s['bars']) if b['date']==t['date'])
            self.assertGreater(index,59)
            if t['side']=='BUY':
                from backend.engine import signals
                self.assertIn('S1',[m for m,_ in signals(s['bars'][:index])])
            self.assertEqual(t['quantity']%100,0)
        short=copy.deepcopy(s);short['bars']=short['bars'][:140]
        a=backtest(short)['trades'];b=[t for t in r['trades'] if t['date']<=short['bars'][-1]['date']]
        self.assertEqual(a,b)
    def test_fees_reduce_equity(self):
        s=self.snapshot['stocks'][0]
        free=backtest(s,commission=0,tax=0,slippage=0)
        charged=backtest(s,commission=.001,tax=.001,slippage=.003)
        self.assertLess(charged['final_equity'],free['final_equity'])
    def test_api_validation(self):
        for change in [{'quantity':-1},{'code':'bad'},{'price':0},{'side':'AUTO'},{'quantity':1.5}]:
            self.assertEqual(self.client.post('/api/trades',json={**self.trade(),**change}).status_code,422)
        self.assertEqual(self.client.post('/api/trades',json=self.trade('SELL')).status_code,400)
        self.assertEqual(self.client.get('/api/daily?mode=bad').status_code,422)
    def test_transactional_api(self):
        self.assertEqual(self.client.post('/api/trades',json=self.trade()).status_code,200)
        self.assertEqual(self.client.get('/api/daily').json()['holdings'][0]['quantity'],100)
    def test_backtest_invalid(self):
        self.assertEqual(self.client.post('/api/backtest',json={'code':'999999'}).status_code,400)
    def test_report_preserves_excluded_holding(self):
        r=daily(self.snapshot,[{'code':'688001','quantity':100,'avg_cost':10}])
        self.assertEqual(len(r['holdings']),1)
    def test_missing_cache_still_lists_holdings(self):
        self.store.record(self.trade(mode='live'))
        with patch('backend.app.load',side_effect=ValueError('行情不可用')):
            response=self.client.get('/api/daily?mode=live')
        self.assertEqual(response.status_code,200)
        r=response.json();self.assertEqual(r['holdings'][0]['code'],'600001')
        self.assertIsNone(r['holdings'][0]['market_value']);self.assertEqual(r['candidates'],[])
    def test_plan_aggregate_budget(self):
        from datetime import timedelta
        snap=copy.deepcopy(self.snapshot)
        now=datetime.now(ZoneInfo('Asia/Shanghai'))
        day=now.date() if now.hour>=16 else now.date()-timedelta(days=1)
        while day.weekday()>4:day-=timedelta(days=1)
        # Shift the synthetic fixture to test fresh-live allocation without remote prices.
        delta=day-date.fromisoformat(snap['as_of'])
        for s in snap['stocks']:
            for b in s['bars']:b['date']=(date.fromisoformat(b['date'])+delta).isoformat()
        snap.update(mode='live',as_of=day.isoformat(),expected_session=day.isoformat(),calendar_verified=True,calendar=[day.isoformat(),(now.date()+timedelta(days=1)).isoformat()])
        r=daily(snap,[])
        self.assertEqual(r['warnings'],[])
        active=[c for c in r['candidates'] if c['suggested_quantity']]
        self.assertTrue(active)
        self.assertEqual(len({c['code'] for c in active}),len(active))
        self.assertLessEqual(sum(c['reference_close']*c['suggested_quantity']*1.005 for c in active),70000)
        self.assertTrue(all(c['reference_close']*c['suggested_quantity']<20000 for c in active))
    def test_scheduled_sync_failure_still_reports_holdings(self):
        import backend.cli as cli
        self.store.record(self.trade(mode='live'))
        with patch('sys.argv',['aquant','daily']),patch.object(cli,'sync_live',side_effect=ConnectionError('offline')),patch.object(cli,'load',side_effect=ValueError('no cache')),patch.object(cli,'Store',return_value=self.store),patch.object(cli,'DATA',Path(self.temp.name)),patch('builtins.print'):
            with self.assertRaises(SystemExit) as result:cli.main()
        self.assertEqual(result.exception.code,1)
        import json
        report=json.loads((Path(self.temp.name)/'report-live.json').read_text(encoding='utf-8'))
        self.assertEqual(report['holdings'][0]['code'],'600001');self.assertIsNone(report['risk']['exposure']);self.assertEqual(report['candidates'],[])

if __name__=='__main__':unittest.main()
