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

if __name__=='__main__':unittest.main()
