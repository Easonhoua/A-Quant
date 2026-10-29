import unittest
from unittest.mock import patch
from backend.engine import features, signals, wilder, backtest, STRATEGY
from backend.data import demo


class IndicatorTests(unittest.TestCase):
    def test_wilder_seed_and_recursion(self):
        self.assertEqual(wilder([1,2,3,6],3),[2,10/3])

    def test_constant_prices_and_prior_channel(self):
        bars=[{'close':10,'open':10,'high':11,'low':9,'volume':100,'amount':1000} for _ in range(60)]
        bars[-1]={**bars[-1],'high':20,'volume':200}
        f=features(bars)
        self.assertEqual(f['rsi'],50)
        self.assertEqual(f['prior_high20'],11)
        self.assertEqual(f['prior_low20'],9)
        self.assertEqual(f['volume_ratio'],2)
        self.assertAlmostEqual(f['atr'],(2*13+11)/14)

    def test_adx_filters_only_s1(self):
        f={'atr':1,'close':11,'prior_high20':10,'volume_ratio':2,'ma20':10,
           'ma60':9,'ma20_previous':9.9,'atr_distance':1,'rsi':50,
           'adx':10,'plus_di':30,'minus_di':10}
        with patch('backend.engine.features',return_value=f):
            self.assertEqual([m for m,_ in signals([{}]*60)],['S1','S2'])
            self.assertEqual([m for m,_ in signals([{}]*60,True)],['S2'])
            f['adx']=30
            self.assertEqual([m for m,_ in signals([{}]*60,True)],['S1','S2'])
            f['rsi']=61
            self.assertEqual([m for m,_ in signals([{}]*60)],['S1'])
            f['atr']=0
            self.assertEqual(signals([{}]*60),[])

    def test_strict_filter_is_subset_and_rejects_weak_trend(self):
        f={'atr':1,'close':11,'previous_close':10.8,'prior_high20':10,'volume_ratio':2,'ma20':10,
           'ma60':9,'ma20_previous':9.9,'atr_distance':1,'rsi':50,
           'adx':10,'plus_di':30,'minus_di':10}
        with patch('backend.engine.features',return_value=f):
            self.assertEqual(len(signals([{}]*60)),2)
            self.assertEqual(signals([{}]*60,strict_filter=True),[])
            f['adx']=30
            self.assertEqual(len(signals([{}]*60,strict_filter=True)),2)
            f['rsi']=80
            self.assertEqual(signals([{}]*60,strict_filter=True),[])
            f['rsi']=50;f['previous_close']=12;f['atr_distance']=4
            self.assertEqual(signals([{}]*60,strict_filter=True),[])

    def test_backtest_entry_uses_previous_atr_and_risk_budget(self):
        stock=demo()['stocks'][0]
        result=backtest(stock)
        buys=[t for t in result['trades'] if t['side']=='BUY']
        self.assertTrue(buys)
        equity=100000
        for t in result['trades']:
            if t['side']=='SELL': continue
            equity=next(x['equity'] for x in reversed(result['curve']) if x['date']<t['date'])
            i=next(i for i,b in enumerate(stock['bars']) if b['date']==t['date'])
            f=features(stock['bars'][:i])
            self.assertLessEqual(t['quantity']*STRATEGY['stop_atr']*f['atr'],equity*.01+1e-6)
            self.assertLessEqual(t['quantity']*t['price'],equity*.20+1)
        self.assertEqual(result['strategy']['adx_filter'],False)

if __name__=='__main__': unittest.main()
