import json, random
from datetime import date, timedelta, datetime
from pathlib import Path
from zoneinfo import ZoneInfo
from .engine import allowed, valid_bars
from .quality import coverage, coverage_warning

ROOT=Path(__file__).resolve().parents[1]
DATA=ROOT/'data'
DATA.mkdir(exist_ok=True)

def demo():
    path=DATA/'demo.json'
    if path.exists():
        cached=json.loads(path.read_text(encoding='utf-8'))
        if cached.get('fixture_version')==2:return cached
    rng=random.Random(42); days=[]; d=date(2025,1,2)
    while len(days)<200:
        if d.weekday()<5: days.append(d.isoformat())
        d+=timedelta(days=1)
    stocks=[]
    for j,(code,name) in enumerate([('600001','示例主板甲'),('000001','示例主板乙'),('300001','示例创业甲'),('301001','示例创业乙'),('688001','示例科创'),('920001','示例北交'),('002001','ST示例'),('600002','示例低流动'),('600003','示例停牌')]):
        bars=[]; price=10+j*2
        for i,day in enumerate(days):
            op=price; price=max(2,op*(1+.0015+rng.gauss(0,.012)))
            pulse=j==0 and i in (80,120,160,199)
            if pulse: price=max(x['high'] for x in bars[-20:])*1.02
            volume=6_000_000*(3 if pulse else 1+rng.random())
            if j==7: volume=100
            if j==8 and i==199: volume=0
            bars.append({'date':day,'open':round(op,2),'high':round(max(op,price)*1.005,2),'low':round(min(op,price)*.995,2),'close':round(price,2),'volume':round(volume),'amount':round(volume*price,2)})
        stocks.append({'code':code,'name':name,'bars':bars})
    result={'mode':'demo','fixture_version':2,'as_of':days[-1],'expected_session':days[-1],'calendar_verified':False,'complete':True,'stocks':stocks,'errors':[]}
    path.write_text(json.dumps(result,ensure_ascii=False),encoding='utf-8'); return result

def load(mode):
    if mode=='demo': return demo()
    path=DATA/'live.json'
    if not path.exists(): raise ValueError('尚无实盘行情，请先运行收盘同步；不会回退为示例数据')
    return json.loads(path.read_text(encoding='utf-8'))

def tencent_bars(frame):
    """Check units against traded value; AKShare versions differ for sz000 stocks."""
    bars=[]
    for _,b in frame.iterrows():
        row={'date':str(b['date'])[:10],**{k:float(b[k]) for k in ('open','high','low','close','volume','amount')}}
        if row['volume']>0:
            candidates=[factor for factor in (1,100) if row['low']*.98 <= row['amount']/(row['volume']*factor) <= row['high']*1.02]
            if len(candidates)!=1: raise ValueError('腾讯成交量/成交额单位校验失败：'+row['date'])
            row['volume']*=candidates[0]
        bars.append(row)
    return bars

def sync_live(provider='eastmoney', codes=None):
    """Explicit CLI only; no silent fallback. Full market scan may take hours."""
    import akshare as ak
    import time
    now=datetime.now(ZoneInfo('Asia/Shanghai'))
    calendar=ak.tool_trade_date_hist_sina()
    sessions=sorted(str(x)[:10] for x in calendar['trade_date'] if str(x)[:10]<=now.date().isoformat())
    if not sessions or max(str(x)[:10] for x in calendar['trade_date'])<now.date().isoformat(): raise ValueError('交易日历覆盖不足')
    if sessions[-1]==now.date().isoformat() and (now.hour,now.minute)<(15,30): sessions=sessions[:-1]
    expected=sessions[-1]
    if provider not in ('eastmoney','tencent'): raise ValueError('未知行情源')
    spot=ak.stock_zh_a_spot() if provider=='tencent' else ak.stock_zh_a_spot_em()
    stocks=[]; errors=[]
    from .store import Store
    held={p['code'] for p in Store().positions('live')}
    seen=set()
    previous={}
    if codes:
        cached=load('live')
        if cached.get('provider')!=provider: raise ValueError('局部补取必须与原缓存数据源一致')
        previous={s['code']:s for s in cached['stocks']}
        if not set(codes)<=set(previous): raise ValueError('补取代码不在原缓存中')
    start=(date.fromisoformat(expected)-timedelta(days=450)).strftime('%Y%m%d')
    for _,row in spot.iterrows():
        code=str(row['代码']).removeprefix('sh').removeprefix('sz').removeprefix('bj').zfill(6); name=str(row['名称'])
        if not allowed(code,name) and code not in held: continue
        seen.add(code)
        if codes and code not in codes:
            if code in previous: stocks.append(previous[code])
            continue
        bars=[]
        try:
            if provider=='tencent':
                frame=ak.stock_zh_a_hist_tx(symbol=('sh' if code.startswith('6') else 'sz')+code,start_date=start,end_date=expected.replace('-',''),adjust='',timeout=20)
                bars=tencent_bars(frame)
            else:
                frame=ak.stock_zh_a_hist(symbol=code,period='daily',start_date=start,end_date=expected.replace('-',''),adjust='')
                for _,b in frame.iterrows():
                    bars.append({'date':str(b['日期'])[:10],**{en:float(b[cn])*(100 if en=='volume' else 1) for en,cn in [('open','开盘'),('high','最高'),('low','最低'),('close','收盘'),('volume','成交量'),('amount','成交额')]}})
            if not bars or not valid_bars(bars): raise ValueError('空行情或日线校验失败')
            if code in previous and bars[-1]['date']<previous[code]['bars'][-1]['date']: raise ValueError('新数据比原缓存更旧，保留原记录')
        except Exception as e:
            errors.append({'code':code,'error':str(e)[:160]})
            if code in previous: bars=previous[code]['bars']
        stocks.append({'code':code,'name':name,'bars':bars})
        time.sleep(.25)
    for code in held-seen:
        errors.append({'code':code,'error':'持仓不在市场快照中，请核实停牌/退市/代码'})
        stocks.append({'code':code,'name':'持仓行情缺失','bars':[]})
    if len(stocks)<1000: raise ValueError('市场覆盖异常，不覆盖原缓存')
    result={'mode':'live','as_of':expected,'expected_session':expected,'calendar_verified':True,'calendar':sorted(str(x)[:10] for x in calendar['trade_date']),'complete':not errors,'stocks':stocks,'errors':errors,'fetched_at':now.isoformat(),'adjust':'不复权'}
    result['provider']=provider
    result['coverage']=coverage(result)
    result['complete']=not errors and not coverage_warning(result)
    result['quality_warnings']=[coverage_warning(result)] if coverage_warning(result) else []
    temp=DATA/'live.tmp'; temp.write_text(json.dumps(result,ensure_ascii=False,allow_nan=False),encoding='utf-8'); temp.replace(DATA/'live.json')
    return {'stocks':len(stocks),'errors':len(errors),'as_of':expected,'complete':result['complete'],'coverage':result['coverage'],'warnings':result['quality_warnings']}
