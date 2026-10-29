import copy
import unittest
from unittest.mock import patch
from backend.data import demo
from backend.engine import daily, backtest
from backend.quality import coverage, coverage_warning, price_anomalies


class QualityIntegrationTests(unittest.TestCase):
    def test_coverage_detects_false_complete(self):
        s={'mode':'live','as_of':'2026-10-28','complete':True,'stocks':[
            {'bars':[{'date':'2026-10-27'}]},{'bars':[{'date':'2026-10-28'}]}]}
        self.assertEqual(coverage(s)['ratio'],.5)
        self.assertIn('1/2',coverage_warning(s))
        s['stocks'][0]['bars'][-1]['date']='2026-10-28'
        self.assertIsNone(coverage_warning(s))

    def test_stale_holding_has_last_known_value_not_current_advice(self):
        s=copy.deepcopy(demo());stock=s['stocks'][0]
        s['as_of']='2026-10-28'
        r=daily(s,[{'code':stock['code'],'quantity':100,'avg_cost':10}])
        h=r['holdings'][0]
        self.assertIsNone(h['market_value'])
        self.assertEqual(h['last_known_market_value'],round(stock['bars'][-1]['close']*100,2))
        self.assertIn('不能据此认定停牌',h['status'])
        self.assertIsNone(r['risk']['exposure'])
        self.assertTrue(all(c['suggested_quantity']==0 for c in r['candidates']))

    def test_price_jump_is_flagged_without_claiming_corporate_action(self):
        self.assertEqual(price_anomalies([{'date':'a','close':100},{'date':'b','close':50}],'002714'),['b'])

    def test_backtest_skips_open_above_plan_limit(self):
        stock=copy.deepcopy(demo()['stocks'][0]);signal_index=65
        next_bar=stock['bars'][signal_index+1];prev=stock['bars'][signal_index]['close']
        next_bar.update(open=prev*1.03,high=prev*1.05,low=prev*.95,close=prev)
        with patch('backend.engine.signals',side_effect=lambda bars,*args: [('S1','test')] if len(bars)==signal_index+1 else []):
            r=backtest(stock)
        self.assertEqual(r['trades'],[])

    def test_liquidity_allocation_and_no_target_prediction(self):
        r=daily(demo(),[])
        self.assertEqual([c['liquidity20'] for c in r['candidates']],sorted([c['liquidity20'] for c in r['candidates']],reverse=True))
        self.assertTrue(all(c['take_profit_reference'] is None for c in r['candidates']))

if __name__=='__main__':unittest.main()
