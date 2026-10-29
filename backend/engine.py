"""Deterministic rule baseline; no network, persistence, or order submission."""
from datetime import datetime, date
from zoneinfo import ZoneInfo
from statistics import mean
from collections import Counter
import math
from .quality import coverage, coverage_warning, price_anomalies

RULES = {'min_amount20': 50_000_000, 'min_bars': 60, 'max_position': .20,
         'max_exposure': .70, 'risk_per_trade': .01, 'min_plan_risk': .003,
         'candidate_top_n': 3}   # 展示名额，属操作参数，不是收益预测

def allowed(code, name):
    return len(code) == 6 and code.isdigit() and code.startswith(('600','601','603','605','000','001','002','003','300','301')) and not any(x in name.upper() for x in ('ST', '退'))

def valid_bars(bars):
    if not bars or len({b['date'] for b in bars}) != len(bars):
        return False
    try:
        return all(date.fromisoformat(b['date']) and all(math.isfinite(float(b[k])) for k in ('open','high','low','close','volume','amount')) and 0 < b['low'] <= min(b['open'], b['close']) <= max(b['open'], b['close']) <= b['high'] and b['volume'] >= 0 and b['amount'] >= 0 for b in bars) and bars == sorted(bars, key=lambda b:b['date'])
    except (ValueError, KeyError, TypeError):
        return False

def freshness(snapshot, now=None):
    now = now or datetime.now(ZoneInfo('Asia/Shanghai'))
    errors = []
    if snapshot.get('mode') == 'demo': errors.append('示例合成行情：禁止用于实际买卖')
    if not snapshot.get('complete'): errors.append('全市场行情不完整：禁止新买入计划')
    if not snapshot.get('calendar_verified'): errors.append('交易日历未验证')
    expected = snapshot.get('expected_session')
    if snapshot.get('mode') == 'live':
        sessions=snapshot.get('calendar',[])
        today=now.date().isoformat()
        eligible=[d for d in sessions if d<today or (d==today and (now.hour,now.minute)>=(15,30))]
        if not sessions or sessions[-1]<today or not eligible:
            errors.append('缓存交易日历覆盖不足，请同步')
        else:
            expected=max(eligible)
    if not expected or snapshot.get('as_of') != expected: errors.append('行情日期与应有收盘交易日不一致')
    try:
        age = (now.date() - date.fromisoformat(snapshot['as_of'])).days
        if age < 0 or age > 4: errors.append('行情过期或日期异常，请重新同步')
        if snapshot['as_of'] == now.date().isoformat() and (now.hour,now.minute) < (15,30): errors.append('尚未完成收盘数据确认')
    except (ValueError, KeyError): errors.append('行情日期无效')
    issue=coverage_warning(snapshot)
    if issue: errors.append(issue)
    return errors

STRATEGY = {'version':'5-candidate-order', 'atr_period':14, 'stop_atr':2.0,
            'donchian_period':20, 'volume_ratio':1.3, 'rsi_period':14,
            's2_rsi_min':40, 's2_rsi_max':60, 's2_max_atr_distance':1.0,
            'adx_period':14, 'adx_min':25, 'max_hold_s1':8, 'max_hold_s2':30, 'max_entry_gap':.01,
            # 信号日振幅/ATR 上限，按模型分别设置：突破当天天然是大振幅（实测 S1 最小 1.77），
            # 用同一个 1.5 会把 S1 全删光；S2 是回调入场，宽振幅=追高，1.5 才有效。
            'max_day_range_atr':{'S1':2.5, 'S2':1.5}}


def market_context(snapshot, window=20):
    """等权指数与其 MA20、以及站上自身 MA20 的股票比例（宽度）。

    只用缓存日线，不发请求。指数是按"全样本每日等权收益"累加出来的相对水平，
    绝对数值没有意义，只与自己的 MA20 比较。仅在最后交易日有 K 线的股票参与，
    样本过少时不把它当作可用信号（见 trustworthy）。
    """
    as_of = snapshot.get('as_of')
    ret_sum = {}; ret_cnt = {}
    above = 0; samples = 0; total = 0
    for s in snapshot.get('stocks', []):
        bars = s.get('bars') or []
        if not bars:
            continue
        total += 1
        if len(bars) < window + 1 or bars[-1]['date'] != as_of:
            continue
        closes = [b['close'] for b in bars]
        if closes[-1] <= 0:
            continue
        samples += 1
        ma = mean(closes[-window:])
        if closes[-1] > ma:
            above += 1
        tail = bars[-(window + 1):]
        for prev, cur in zip(tail, tail[1:]):
            if prev['close'] <= 0:
                continue
            day = cur['date']
            ret_sum[day] = ret_sum.get(day, 0.0) + cur['close'] / prev['close'] - 1
            ret_cnt[day] = ret_cnt.get(day, 0) + 1
    levels = [1.0]
    for day in sorted(ret_sum):
        if ret_cnt.get(day):
            levels.append(levels[-1] * (1 + ret_sum[day] / ret_cnt[day]))
    index_above = bool(len(levels) >= window and levels[-1] > mean(levels[-window:]))
    breadth = above / samples if samples else 0.0
    return {'as_of': as_of, 'window': window,
            'breadth': round(breadth, 4), 'index_above_ma20': index_above,
            'index_ret20': round(levels[-1] / levels[0] - 1, 5) if len(levels) > 1 else None,
            'samples': samples, 'total': total,
            'trustworthy': bool(total and samples >= total * .5),
            'regime': '强势' if (index_above and breadth >= .5) else ('偏弱' if not index_above else '混合')}


def wilder(values, period):
    if len(values)<period: return []
    result=[mean(values[:period])]
    for value in values[period:]: result.append((result[-1]*(period-1)+value)/period)
    return result


def features(bars):
    c=[b['close'] for b in bars]
    n=STRATEGY['atr_period']; r=STRATEGY['rsi_period']; d=STRATEGY['adx_period']
    tr=[]; plus=[]; minus=[]
    for previous,current in zip(bars,bars[1:]):
        tr.append(max(current['high']-current['low'],abs(current['high']-previous['close']),abs(current['low']-previous['close'])))
        up=current['high']-previous['high']; down=previous['low']-current['low']
        plus.append(up if up>down and up>0 else 0)
        minus.append(down if down>up and down>0 else 0)
    atr=wilder(tr,n)[-1]
    changes=[y-x for x,y in zip(c,c[1:])]
    gain=wilder([max(0,x) for x in changes],r)[-1]
    loss=wilder([max(0,-x) for x in changes],r)[-1]
    rsi=50 if gain==loss==0 else (100 if loss==0 else 100-100/(1+gain/loss))
    trs=wilder(tr,d); ps=wilder(plus,d); ms=wilder(minus,d)
    pdi=[100*p/t if t else 0 for p,t in zip(ps,trs)]
    mdi=[100*m/t if t else 0 for m,t in zip(ms,trs)]
    dx=[100*abs(p-m)/(p+m) if p+m else 0 for p,m in zip(pdi,mdi)]
    adx=wilder(dx,d)[-1] if len(dx)>=d else 0
    v=mean(b['volume'] for b in bars[-21:-1])
    channel=bars[-STRATEGY['donchian_period']-1:-1]
    return {'close':c[-1], 'ma5':mean(c[-5:]), 'ma20':mean(c[-20:]), 'ma60':mean(c[-60:]),
            'ma20_previous':mean(c[-21:-1]), 'previous_close':c[-2], 'atr':atr, 'rsi':rsi, 'adx':adx,
            'plus_di':pdi[-1], 'minus_di':mdi[-1],
            'atr_distance':(c[-1]-mean(c[-20:]))/atr if atr else None,
            'amount20':mean(b['amount'] for b in bars[-20:]), 'volume_ratio':bars[-1]['volume']/v if v else 0,
            'return20':c[-1]/c[-21]-1, 'prior_high20':max(b['high'] for b in channel),
            'prior_low20':min(b['low'] for b in channel)}


def signals(bars, adx_filter=False, strict_filter=False):
    if len(bars)<60: return []
    return signals_from_features(features(bars),adx_filter,strict_filter)


def signals_from_features(f, adx_filter=False, strict_filter=False):
    out=[]
    if f['atr']<=0: return out
    if (f['close']>f['prior_high20'] and f['volume_ratio']>=STRATEGY['volume_ratio']
            and f['close']>f['ma20'] and (not adx_filter or
            (f['adx']>=STRATEGY['adx_min'] and f['plus_di']>f['minus_di']))):
        out.append(('S1', '突破前20日唐奇安上轨，量比≥1.3，收盘高于SMA20；2ATR风控'+('；ADX≥25且+DI>−DI' if adx_filter else '')))
    if (f['ma20']>f['ma60'] and f['ma20']>f['ma20_previous']
            and 0<=f['atr_distance']<=STRATEGY['s2_max_atr_distance']
            and STRATEGY['s2_rsi_min']<=f['rsi']<=STRATEGY['s2_rsi_max']):
        out.append(('S2', 'SMA20>SMA60且SMA20上行，收盘距SMA20为0–1ATR，RSI14为40–60；2ATR风控'))
    if strict_filter:
        out=[(model,reason+'；严格复筛通过') for model,reason in out if
             (model=='S1' and f['adx']>=25 and f['plus_di']>f['minus_di']
              and f['rsi']<=75 and f['atr_distance']<=3)
             or (model=='S2' and f['adx']>=20 and f['plus_di']>f['minus_di']
                 and f['close']>f['previous_close'])]
    return out


def stop_reference(price, atr):
    # Round upward so the displayed stop does not understate planned risk.
    return math.ceil((price-STRATEGY['stop_atr']*atr)*100)/100


def daily(snapshot, positions, capital=100000, adx_filter=False, strict_filter=False,
          amplitude_filter=False, market_filter=False, top_n=None, model_filter='ALL'):
    """持仓优先的收盘日报。

    amplitude_filter：信号日振幅超过该模型上限（S1 2.5×ATR / S2 1.5×ATR）时不计入候选。
    market_filter：等权指数低于自身 MA20 时把候选标为"仅研究"。
    top_n：每日展示/分配名额，默认取 RULES['candidate_top_n']；其余进 candidates_overflow。
    model_filter：'ALL' 全部、'S1' 只要突破、'S2' 只要回调。
    """
    if model_filter not in ('ALL', 'S1', 'S2'):
        raise ValueError("model_filter 只能是 ALL / S1 / S2")
    warnings=freshness(snapshot)
    stocks={s['code']:s for s in snapshot['stocks']}
    holdings=[]; value=0; cost_value=0; unknown=False
    # This phase MUST finish for every holding before scanning any candidates.
    for p in positions:
        s=stocks.get(p['code']); bars=s.get('bars',[]) if s else []
        cost_value+=p['quantity']*p['avg_cost']
        problem=None
        if not bars: problem='缓存中没有该股票日线'
        elif not valid_bars(bars): problem='日线格式或价格数据异常'
        elif len(bars)<60: problem=f'历史不足60根（现有{len(bars)}根）'
        elif bars[-1]['date']!=snapshot['as_of']: problem=f"目标日期{snapshot['as_of']}，个股最新日期{bars[-1]['date']}：尚缺目标日K线，不能据此认定停牌"
        elif bars[-1]['volume']<=0: problem='最新日线成交量为0，需核实是否停牌'
        if problem:
            unknown=True
            last=bars[-1] if bars and valid_bars(bars) else None
            holdings.append({**p,'status':problem,'action':'暂停形成当日价格建议；补齐行情后复核',
                             'market_value':None,'pnl':None,'valuation_date':last['date'] if last else None,
                             'last_known_close':last['close'] if last else None,
                             'last_known_market_value':round(last['close']*p['quantity'],2) if last else None})
            continue
        f=features(bars); mv=f['close']*p['quantity']; value+=mv
        anomalies=price_anomalies(bars[-61:],p['code'])
        stop=max(0,stop_reference(p['avg_cost'],f['atr']))
        action='继续观察；次日重新检查'
        if f['close']<=stop: action='已触及成本减2倍当前ATR参考线（非锁定止损）：下个可交易时段评估减仓/退出；跌停可能无法成交'
        elif f['close']<f['ma20']: action='跌破20日均线：评估减仓，避免继续加仓'
        if anomalies:
            stop=None; action='近期存在异常价格跳变，可能为除权或数据问题；核实前暂停技术建议'
            warnings.append(p['code']+'近期异常价格跳变待核实：暂停新增计划')
        if warnings: action='行情仅供复核，暂停新增操作；'+action
        holdings.append({**p,'status':'已分析','close':f['close'],'market_value':round(mv,2),'pnl':round(mv-p['quantity']*p['avg_cost'],2),'stop':stop,'action':action,'valuation_date':bars[-1]['date'],'price_anomalies':anomalies})
    if unknown: warnings.append('至少一笔持仓无法估值：已阻止全部新买入计划')
    exposure=value/capital
    risk={'capital':capital,'known_market_value':round(value,2),'exposure':None if unknown else round(exposure,4),
          'largest_weight':None if unknown else max([h['market_value']/capital for h in holdings] or [0]),
          'warnings':(['总仓位超过70%'] if exposure>RULES['max_exposure'] else [])+(['单股超过20%'] if any((h.get('market_value') or 0)/capital>.2 for h in holdings) else [])+(['存在无法估值持仓'] if unknown else [])}
    market=market_context(snapshot)
    pool=[]; excluded=[]; found=[]; rejected_range=Counter()
    for s in snapshot['stocks']:
        bars=s.get('bars',[]); reason=None
        if not allowed(s['code'],s['name']): reason='板块/ST/退市过滤'
        elif not valid_bars(bars) or len(bars)<RULES['min_bars']: reason='历史不足或数据异常'
        elif bars[-1]['date']!=snapshot['as_of'] or bars[-1]['volume']<=0: reason='停牌或个股行情过期'
        elif price_anomalies(bars[-61:],s['code']): reason='近60日异常价格跳变，待核实除权或数据质量'
        elif features(bars)['amount20']<RULES['min_amount20']: reason='20日平均成交额不足5000万元'
        if reason: excluded.append({'code':s['code'],'reason':reason}); continue
        pool.append(s['code'])
        if any(p['code']==s['code'] for p in positions): continue
        for model,reason in signals(bars,adx_filter,strict_filter):
            f=features(bars); close=f['close']; stop=stop_reference(close,f['atr'])
            if not 0<stop<close: continue
            # 信号日振幅/ATR：突破当天天然是大振幅，所以上限按模型分开设
            day_range=(bars[-1]['high']-bars[-1]['low'])/f['atr'] if f['atr'] else None
            limit_range=STRATEGY['max_day_range_atr'].get(model)
            if amplitude_filter and day_range is not None and limit_range and day_range>limit_range:
                rejected_range[model]+=1; continue
            found.append({'code':s['code'],'name':s['name'],'model':model,'reason':reason,'reference_close':close,'stop_reference':stop,'take_profit_reference':None,'risk_distance':round(close-stop,4),'atr':f['atr'],'rsi':f['rsi'],'adx':f['adx'],'liquidity20':f['amount20'],'day_range_atr':round(day_range,2) if day_range is not None else None})
    by_model=Counter(c['model'] for c in found)
    if model_filter!='ALL': found=[c for c in found if c['model']==model_filter]
    # Operational liquidity order, explicitly not a predicted-return score.
    found.sort(key=lambda c:(-c['liquidity20'],c['code'],c['model']))
    limit=max(1,int(RULES['candidate_top_n'] if top_n is None else top_n))
    candidates=found[:limit]; overflow=found[limit:]
    weak=bool(market_filter and market['trustworthy'] and not market['index_above_ma20'])
    # Allocate sequentially: suggestions cannot collectively exceed remaining exposure.
    remaining=max(0,capital*.70-value); allocated=set()
    for c in candidates:
        budget=min(capital*.20,remaining)
        risk_quantity=capital*RULES['risk_per_trade']/(c['reference_close']-c['stop_reference'])
        qty=max(0,int(min(budget/(c['reference_close']*1.005),risk_quantity)/100)*100)
        duplicate=c['code'] in allocated
        too_small=qty>0 and qty*(c['reference_close']-c['stop_reference'])<capital*RULES['min_plan_risk']
        if too_small: qty=0
        blocked=bool(warnings) or duplicate or weak
        c['suggested_quantity']=0 if blocked else qty
        c['plan_status']=('市场状态偏弱（等权指数低于MA20）：仅研究' if weak else
                          '同股已有计划，未重复分配' if duplicate else
                          '仅研究/禁止买入' if blocked else
                          '条件观察计划' if qty else
                          '低于最小计划风险预算' if too_small else '仓位或整手预算不足')
        c['execution']=f'按20日均成交额降序进入前{limit}名（非收益排名）；下一交易日仅在可成交且不高于参考收盘价1%时人工复核；跳空、涨跌停、公告风险需重新评估'
        if not blocked and qty: remaining-=qty*c['reference_close']*1.005; allocated.add(c['code'])
    for c in overflow:
        c['suggested_quantity']=0
        c['plan_status']='未进入前%d名，仅备查' % limit
        c['execution']='按20日均成交额排序排在名额之外；需要时可调大 top_n 查看，不进入当日预算分配'
    return {'as_of':snapshot['as_of'],'mode':snapshot['mode'],'warnings':warnings,'source_errors':snapshot.get('errors',[]),'sequence':['持仓逐一分析','组合风险','新选股'],
            'holdings':holdings,'risk':risk,'pool_count':len(pool),'excluded':excluded,'candidates':candidates,'candidates_overflow':overflow,
            'candidates_total':len(found),'candidates_total_all':sum(by_model.values()),
            'candidates_by_model':dict(by_model),'model_filter':model_filter,
            'candidate_limit':limit,'market':market,
            'signal_stats':{'found':len(found),'by_model':dict(by_model),
                            'rejected_day_range':sum(rejected_range.values()),
                            'rejected_by_model':dict(rejected_range),
                            'amplitude_limits':STRATEGY['max_day_range_atr'],
                            'top_n':limit,'model_filter':model_filter,
                            'market_filter':bool(market_filter),'amplitude_filter':bool(amplitude_filter),
                            'market_blocks':weak},
            'data_quality':coverage(snapshot),'rules':RULES,
            'strategy':{**STRATEGY,'adx_filter':adx_filter,'strict_filter':strict_filter,'model_filter':model_filter}}

def backtest(stock, model='S1', capital=100000, commission=.0003, tax=.0005, slippage=.001, adx_filter=False, strict_filter=False):
    """Single asset long-only, signal on t close, fill t+1 open; T+1, lot size and conservative limit gates."""
    bars=stock['bars']
    if model not in ('S1','S2') or not valid_bars(bars) or len(bars)<62: raise ValueError('模型或日线数据无效/不足62根')
    cash=capital; qty=0; entry=0; pending=None; trades=[]; curve=[]; peak=capital; dd=0; entry_index=-1; skipped=0; entry_stop=0; pending_atr=0
    for i,b in enumerate(bars):
        if i and pending:
            prev=bars[i-1]['close']; limit=.20 if stock['code'].startswith(('300','301')) else .10
            # Conservative guard blocks near limit even if intraday fills might be possible.
            tradable=b['volume']>0 and abs(b['open']/prev-1)<limit-.005
            if tradable:
                if pending=='BUY' and not qty and b['open']/prev-1<=STRATEGY['max_entry_gap']:
                    price=b['open']*(1+slippage)
                    equity=cash
                    q=int(max(0,min((cash-5)/(price*(1+commission)),equity*.20/price,equity*.01/(STRATEGY['stop_atr']*pending_atr)))/100)*100
                    entry_stop=stop_reference(price,pending_atr)
                    if entry_stop<=0 or q*(price-entry_stop)<equity*RULES['min_plan_risk']: q=0
                    if q>0:
                        fee=max(5,q*price*commission); cash-=q*price+fee; qty=q; entry=price; entry_index=i
                        trades.append({'date':b['date'],'side':'BUY','quantity':q,'price':round(price,4),'fee':round(fee,2)})
                elif pending=='SELL' and qty and i>entry_index:
                    price=b['open']*(1-slippage); fee=max(5,qty*price*commission)+qty*price*tax
                    cash+=qty*price-fee; trades.append({'date':b['date'],'side':'SELL','quantity':qty,'price':round(price,4),'fee':round(fee,2)}); qty=0
            else: skipped+=1
            pending=None
        eq=cash+qty*b['close']; peak=max(peak,eq); dd=max(dd,1-eq/peak); curve.append({'date':b['date'],'equity':round(eq,2)})
        if i>=59:
            history=bars[:i+1]
            if qty:
                if b['close']<=entry_stop or b['close']<mean(x['close'] for x in history[-20:]) or i-entry_index>=(STRATEGY['max_hold_s1'] if model=='S1' else STRATEGY['max_hold_s2']): pending='SELL'
            elif b['volume']>0 and not price_anomalies(history[-61:],stock['code']) and features(history)['amount20']>=RULES['min_amount20'] and any(m==model for m,_ in signals(history,adx_filter,strict_filter)):
                pending='BUY'; pending_atr=features(history)['atr']
    return {'model':model,'code':stock['code'],'initial_capital':capital,'final_equity':curve[-1]['equity'],'total_return':round(curve[-1]['equity']/capital-1,6),'max_drawdown':round(dd,6),'trades':trades,'curve':curve,'open_quantity':qty,'skipped_orders':skipped,
            'strategy':{**STRATEGY,'adx_filter':adx_filter,'strict_filter':strict_filter},'assumptions':{'position_cap':.20,'risk_budget':.01,'stop':'入场前ATR锁定2倍距离；收盘触发次日开盘退出','commission':commission,'sell_tax':tax,'slippage':slippage,'min_commission':5,'max_entry_gap':STRATEGY['max_entry_gap']},
            'limitations':['单股规则验证，不代表组合收益','当前股票选择含幸存者偏差；无历史ST/退市状态库','未建模除权分红、历史费率变化、市场冲击；费用为可调假设','次日开盘成交；保守涨跌停过滤；未平仓按末日收盘估值']}
