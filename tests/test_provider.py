"""Mock AKShare boundary; temporary caches, no remote requests."""
import unittest,tempfile,json
from pathlib import Path
from datetime import datetime,timedelta
from zoneinfo import ZoneInfo
from unittest.mock import patch,MagicMock
import backend.data as data
try:
    import pandas as pd
    import akshare
    HAS_AK=True
except ImportError:
    HAS_AK=False

@unittest.skipUnless(HAS_AK,'optional AKShare dependencies not installed')
class ProviderTests(unittest.TestCase):
    def test_tencent_volume_units_and_rejection(self):
        frame=pd.DataFrame([{'date':'2026-10-27','open':11.27,'close':11.47,'high':11.55,'low':11.24,'volume':1529292,'amount':1750404200}])
        self.assertEqual(data.tencent_bars(frame)[0]['volume'],152929200)
        frame['volume']=152929200
        self.assertEqual(data.tencent_bars(frame)[0]['volume'],152929200)
        frame['amount']=1
        with self.assertRaises(ValueError):data.tencent_bars(frame)

    def test_tencent_sync_uses_sina_names_and_preserves_st_holdings(self):
        now=datetime.now(ZoneInfo('Asia/Shanghai')).date()
        dates=[now-timedelta(days=1),now,now+timedelta(days=1)]
        spot=pd.DataFrame([{'代码':'sh'+str(600000+i),'名称':'正常'} for i in range(1000)]+[{'代码':'sz002001','名称':'ST持仓'}])
        bars=pd.DataFrame([{'date':dates[0],'open':10,'high':11,'low':9,'close':10,'volume':123,'amount':123000}])
        fake=MagicMock();fake.positions.return_value=[{'code':'002001','quantity':100,'avg_cost':10}]
        with tempfile.TemporaryDirectory() as folder,patch.object(data,'DATA',Path(folder)),patch('backend.store.Store',return_value=fake),patch('akshare.tool_trade_date_hist_sina',return_value=pd.DataFrame({'trade_date':dates})),patch('akshare.stock_zh_a_spot',return_value=spot),patch('akshare.stock_zh_a_hist_tx',return_value=bars),patch('time.sleep'):
            result=data.sync_live('tencent');cached=json.loads((Path(folder)/'live.json').read_text(encoding='utf-8'))
        self.assertEqual(result['stocks'],1001)
        self.assertEqual(cached['provider'],'tencent')
        self.assertEqual(cached['stocks'][-1]['code'],'002001')
        self.assertEqual(cached['stocks'][0]['bars'][0]['volume'],12300)

    def test_full_sync_keeps_st_holding_and_converts_lots(self):
        now=datetime.now(ZoneInfo('Asia/Shanghai')).date()
        dates=[now-timedelta(days=1),now,now+timedelta(days=1)]
        spot=pd.DataFrame([{'代码':str(600000+i),'名称':'正常'} for i in range(1000)]+[{'代码':'002001','名称':'ST持仓'}])
        bars=pd.DataFrame([{'日期':dates[0],'开盘':10,'最高':11,'最低':9,'收盘':10,'成交量':123,'成交额':123000}])
        fake=MagicMock();fake.positions.return_value=[{'code':'002001','quantity':100,'avg_cost':10}]
        with tempfile.TemporaryDirectory() as folder,patch.object(data,'DATA',Path(folder)),patch('backend.store.Store',return_value=fake),patch('akshare.tool_trade_date_hist_sina',return_value=pd.DataFrame({'trade_date':dates})),patch('akshare.stock_zh_a_spot_em',return_value=spot),patch('akshare.stock_zh_a_hist',return_value=bars),patch('time.sleep'):
            result=data.sync_live();cached=json.loads((Path(folder)/'live.json').read_text(encoding='utf-8'))
        self.assertEqual(result['stocks'],1001)
        self.assertIn('002001',[s['code'] for s in cached['stocks']])
        self.assertEqual(cached['stocks'][0]['bars'][0]['volume'],12300)
        self.assertFalse(cached['complete'])
        self.assertEqual(cached['coverage']['covered'],0)
        self.assertTrue(cached['quality_warnings'])
    def test_calendar_failure_preserves_previous_cache(self):
        with tempfile.TemporaryDirectory() as folder,patch.object(data,'DATA',Path(folder)),patch('akshare.tool_trade_date_hist_sina',side_effect=ConnectionError('offline')):
            path=Path(folder)/'live.json';path.write_text('previous-cache')
            with self.assertRaises(ConnectionError):data.sync_live()
            self.assertEqual(path.read_text(),'previous-cache')

    # 以下为增量同步：缓存里已有的交易日不应重复下载
    @staticmethod
    def _expected():
        now=datetime.now(ZoneInfo('Asia/Shanghai'))
        return now.date() if (now.hour,now.minute)>=(15,30) else now.date()-timedelta(days=1)

    def _cache(self, expected, last, provider='tencent'):
        bar={'date':last.isoformat(),'open':10,'high':11,'low':9,'close':10,'volume':1000,'amount':10000}
        return {'mode':'live','provider':provider,'as_of':last.isoformat(),'expected_session':last.isoformat(),
                'calendar_verified':True,'calendar':[(expected-timedelta(days=1)).isoformat(),expected.isoformat(),(expected+timedelta(days=1)).isoformat()],
                'complete':True,'errors':[],
                'stocks':[{'code':str(600000+i),'name':'正常','bars':[dict(bar)]} for i in range(1000)]}

    def _spot(self):
        return pd.DataFrame([{'代码':'sh'+str(600000+i),'名称':'正常'} for i in range(1000)])

    def test_incremental_sync_requests_only_missing_sessions(self):
        expected=self._expected(); last=expected-timedelta(days=1)
        dates=[last,expected,expected+timedelta(days=1)]
        calls=[]
        def fake(symbol,start_date,end_date,adjust,timeout):
            calls.append((start_date,end_date))
            return pd.DataFrame([{'date':expected,'open':10,'high':11,'low':9,'close':10.5,'volume':1000,'amount':10500}])
        store=MagicMock();store.positions.return_value=[]
        with tempfile.TemporaryDirectory() as folder,patch.object(data,'DATA',Path(folder)),patch('backend.store.Store',return_value=store),patch('akshare.tool_trade_date_hist_sina',return_value=pd.DataFrame({'trade_date':dates})),patch('akshare.stock_zh_a_spot',return_value=self._spot()),patch('akshare.stock_zh_a_hist_tx',side_effect=fake),patch('time.sleep'):
            (Path(folder)/'live.json').write_text(json.dumps(self._cache(expected,last)),encoding='utf-8')
            result=data.sync_live('tencent');after=json.loads((Path(folder)/'live.json').read_text(encoding='utf-8'))
        self.assertTrue(result['incremental'])
        self.assertEqual(result['requests'],1000)
        self.assertTrue(calls and all(c==(expected.strftime('%Y%m%d'),expected.strftime('%Y%m%d')) for c in calls))
        self.assertEqual([b['date'] for b in after['stocks'][0]['bars']],[last.isoformat(),expected.isoformat()])

    def test_sync_with_current_cache_makes_no_requests(self):
        expected=self._expected(); dates=[expected-timedelta(days=1),expected,expected+timedelta(days=1)]
        store=MagicMock();store.positions.return_value=[]
        with tempfile.TemporaryDirectory() as folder,patch.object(data,'DATA',Path(folder)),patch('backend.store.Store',return_value=store),patch('akshare.tool_trade_date_hist_sina',return_value=pd.DataFrame({'trade_date':dates})),patch('akshare.stock_zh_a_spot',return_value=self._spot()),patch('akshare.stock_zh_a_hist_tx',side_effect=AssertionError('不应发请求')),patch('time.sleep'):
            (Path(folder)/'live.json').write_text(json.dumps(self._cache(expected,expected)),encoding='utf-8')
            result=data.sync_live('tencent')
        self.assertEqual(result['requests'],0)
        self.assertEqual(result['errors'],0)
        self.assertTrue(result['complete'])

    def test_empty_incremental_response_is_not_an_error(self):
        expected=self._expected(); last=expected-timedelta(days=1)
        dates=[last,expected,expected+timedelta(days=1)]
        empty=pd.DataFrame(columns=['date','open','high','low','close','volume','amount'])
        store=MagicMock();store.positions.return_value=[]
        with tempfile.TemporaryDirectory() as folder,patch.object(data,'DATA',Path(folder)),patch('backend.store.Store',return_value=store),patch('akshare.tool_trade_date_hist_sina',return_value=pd.DataFrame({'trade_date':dates})),patch('akshare.stock_zh_a_spot',return_value=self._spot()),patch('akshare.stock_zh_a_hist_tx',return_value=empty),patch('time.sleep'):
            (Path(folder)/'live.json').write_text(json.dumps(self._cache(expected,last)),encoding='utf-8')
            result=data.sync_live('tencent');after=json.loads((Path(folder)/'live.json').read_text(encoding='utf-8'))
        self.assertEqual(result['errors'],0)
        self.assertEqual(after['stocks'][0]['bars'][-1]['date'],last.isoformat())
        self.assertFalse(result['complete'])  # 覆盖率不足仍然拦住，不静默放行

    def test_provider_change_forces_full_refetch(self):
        expected=self._expected(); last=expected-timedelta(days=1)
        dates=[last,expected,expected+timedelta(days=1)]
        calls=[]
        def fake(symbol,start_date,end_date,adjust,timeout):
            calls.append((start_date,end_date))
            return pd.DataFrame([{'date':expected,'open':10,'high':11,'low':9,'close':10.5,'volume':1000,'amount':10500}])
        store=MagicMock();store.positions.return_value=[]
        with tempfile.TemporaryDirectory() as folder,patch.object(data,'DATA',Path(folder)),patch('backend.store.Store',return_value=store),patch('akshare.tool_trade_date_hist_sina',return_value=pd.DataFrame({'trade_date':dates})),patch('akshare.stock_zh_a_spot',return_value=self._spot()),patch('akshare.stock_zh_a_hist_tx',side_effect=fake),patch('time.sleep'):
            (Path(folder)/'live.json').write_text(json.dumps(self._cache(expected,last,provider='eastmoney')),encoding='utf-8')
            result=data.sync_live('tencent');after=json.loads((Path(folder)/'live.json').read_text(encoding='utf-8'))
        full_start=(expected-timedelta(days=450)).strftime('%Y%m%d')
        self.assertTrue(calls and all(c[0]==full_start and c[1]==expected.strftime('%Y%m%d') for c in calls))
        self.assertEqual(after['provider'],'tencent')
        self.assertEqual([b['date'] for b in after['stocks'][0]['bars']],[expected.isoformat()])
        self.assertFalse(result['incremental'])

if __name__=='__main__':unittest.main()
