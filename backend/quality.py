"""Local diagnostics; never infer suspension or corporate actions from absence alone."""
from collections import Counter

MIN_COVERAGE = .98  # operational guard, not a trading parameter


def coverage(snapshot):
    stocks=snapshot.get('stocks',[]); expected=snapshot.get('as_of')
    last=Counter(s['bars'][-1]['date'] if s.get('bars') else 'EMPTY' for s in stocks)
    count=last.get(expected,0); total=len(stocks)
    return {'expected':expected,'covered':count,'total':total,
            'ratio':count/total if total else 0,'minimum':MIN_COVERAGE,
            'last_dates':dict(last)}


def coverage_warning(snapshot):
    c=coverage(snapshot)
    if snapshot.get('mode')=='live' and c['ratio']<MIN_COVERAGE:
        return f"收盘K线覆盖不足：{c['covered']}/{c['total']}（{c['ratio']:.1%}），目标日期{c['expected']}；供应商可能尚未更新，不能据此认定停牌；禁止新计划"
    return None


def price_anomalies(bars, code):
    limit=.20 if code.startswith(('300','301')) else .10
    return [b['date'] for a,b in zip(bars,bars[1:])
            if a['close']>0 and abs(b['close']/a['close']-1)>limit+.011]
