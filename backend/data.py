import json, os, random
from datetime import date, timedelta, datetime
from pathlib import Path
from zoneinfo import ZoneInfo
from .engine import allowed, valid_bars
from .quality import coverage, coverage_warning

ROOT=Path(__file__).resolve().parents[1]
DATA=ROOT/'data'
DATA.mkdir(exist_ok=True)

# akshare 的历史行情函数每只股票都会打一行 tqdm 进度条；并行同步时这些进度条会互相
# 覆盖，看不出进度。统一关掉，由 sync_live 自己按完成比例输出。
os.environ.setdefault('TQDM_DISABLE','1')

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

def _clip(bars, start_iso):
    """只保留 >= start_iso 的 K 线，使增量同步后的窗口与整段重取完全一致。"""
    return [b for b in bars if b['date'] >= start_iso]


def _merge_bars(previous, fresh):
    """按日期合并：增量区间之外的历史原样保留，同日以新数据为准。"""
    by_date={b['date']:b for b in previous}
    by_date.update({b['date']:b for b in fresh})
    return [by_date[day] for day in sorted(by_date)]


def sync_live(provider='eastmoney', codes=None, full=False, workers=None):
    """Explicit CLI only; no silent fallback.

    默认增量：只从缓存里最后一根 K 线之后开始取，已完成的交易日不重复下载。首次同步、
    full=True、或缓存来自另一个数据源时仍然整段重取 450 天。增量区间返回空不是错误
    （停牌/当日无成交），保留缓存原记录并交由覆盖率统一告警；真实失败才计入 errors。
    并行数影响限流风险，--workers 1 可恢复串行。
    """
    import akshare as ak
    import sys, time
    from concurrent.futures import ThreadPoolExecutor, as_completed
    now=datetime.now(ZoneInfo('Asia/Shanghai'))
    calendar=ak.tool_trade_date_hist_sina()
    sessions=sorted(str(x)[:10] for x in calendar['trade_date'] if str(x)[:10]<=now.date().isoformat())
    if not sessions or max(str(x)[:10] for x in calendar['trade_date'])<now.date().isoformat(): raise ValueError('交易日历覆盖不足')
    if sessions[-1]==now.date().isoformat() and (now.hour,now.minute)<(15,30): sessions=sessions[:-1]
    expected=sessions[-1]
    if provider not in ('eastmoney','tencent'): raise ValueError('未知行情源')
    spot=ak.stock_zh_a_spot() if provider=='tencent' else ak.stock_zh_a_spot_em()
    from .store import Store
    held={p['code'] for p in Store().positions('live')}
    previous={}
    try: cached=load('live')
    except (ValueError,OSError): cached=None
    if cached and cached.get('provider')==provider:
        previous={s['code']:s for s in cached.get('stocks',[])}
    elif cached:
        print('  缓存数据源与本次不一致，改为整段重取',file=sys.stderr)
    if codes:
        if not previous: raise ValueError('局部补取必须与原缓存同源，请先整段同步一次')
        if not set(codes)<=set(previous): raise ValueError('补取代码不在原缓存中')
    expected_day=date.fromisoformat(expected)
    start_day=expected_day-timedelta(days=450)
    start=start_day.strftime('%Y%m%d'); start_iso=start_day.isoformat()
    workers=max(1,min(16,int(workers or os.environ.get('AQUANT_SYNC_WORKERS') or 8)))
    rows=[]; seen=set()
    for _,row in spot.iterrows():
        code=str(row['代码']).removeprefix('sh').removeprefix('sz').removeprefix('bj').zfill(6); name=str(row['名称'])
        if not allowed(code,name) and code not in held: continue
        seen.add(code); rows.append((code,name))
    results={}; todo=[]
    for code,name in rows:
        if codes and code not in codes:
            if code in previous: results[code]=previous[code]
            continue
        prev=previous.get(code); prev_bars=_clip(prev['bars'],start_iso) if prev and prev.get('bars') else []
        # 缓存已经包含目标交易日：不发请求，直接复用
        if not full and prev_bars and prev_bars[-1]['date']>=expected:
            results[code]={'code':code,'name':name,'bars':prev_bars}; continue
        todo.append((code,name))

    def fetch(code,name):
        prev=previous.get(code); prev_bars=_clip(prev['bars'],start_iso) if prev and prev.get('bars') else []
        # 只追加缺失区间；首次/整段重取才回到 450 天前
        begin=start
        if not full and prev_bars: begin=(date.fromisoformat(prev_bars[-1]['date'])+timedelta(days=1)).strftime('%Y%m%d')
        fresh=[]; error=None
        for attempt in (1,2):
            try:
                if provider=='tencent':
                    frame=ak.stock_zh_a_hist_tx(symbol=('sh' if code.startswith('6') else 'sz')+code,start_date=begin,end_date=expected.replace('-',''),adjust='',timeout=20)
                    fresh=tencent_bars(frame)
                else:
                    frame=ak.stock_zh_a_hist(symbol=code,period='daily',start_date=begin,end_date=expected.replace('-',''),adjust='')
                    fresh=[{'date':str(b['日期'])[:10],**{en:float(b[cn])*(100 if en=='volume' else 1) for en,cn in [('open','开盘'),('high','最高'),('low','最低'),('close','收盘'),('volume','成交量'),('amount','成交额')]}} for _,b in frame.iterrows()]
                error=None; break
            except Exception as e:
                error=e; fresh=[]
                if attempt==1: time.sleep(.8)
        if error is not None:
            return {'code':code,'name':name,'bars':prev_bars},{'code':code,'error':str(error)[:160]}
        try:
            if not fresh:
                # 增量区间没有新 K 线 = 停牌或当日无成交，保留原记录，不算错误
                if prev_bars and begin!=start: return {'code':code,'name':name,'bars':prev_bars},None
                raise ValueError('空行情或日线校验失败')
            bars=_merge_bars(prev_bars,fresh) if begin!=start else list(fresh)
            bars=_clip(bars,start_iso)
            if not valid_bars(bars): raise ValueError('日线校验失败')
            if prev_bars and bars[-1]['date']<prev_bars[-1]['date']: raise ValueError('新数据比原缓存更旧，保留原记录')
            return {'code':code,'name':name,'bars':bars},None
        except Exception as e:
            return {'code':code,'name':name,'bars':prev_bars},{'code':code,'error':str(e)[:160]}

    errors=[]
    if todo:
        print(f"  同步 {len(todo)} 只（as_of {expected}{'，整段重取' if not previous or full else '，只取缺失交易日'}）",file=sys.stderr,flush=True)
        total=len(todo); done=0
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures=[pool.submit(fetch,code,name) for code,name in todo]
            for future in as_completed(futures):
                payload,error=future.result()
                results[payload['code']]=payload
                if error: errors.append(error)
                done+=1
                if done%500==0 and done<total: print(f'  已同步 {done}/{total}',file=sys.stderr,flush=True)
        print(f'  已完成 {total}/{total}',file=sys.stderr,flush=True)
    stocks=[results[code] for code,_ in rows if code in results]
    for code in held-seen:
        errors.append({'code':code,'error':'持仓不在市场快照中，请核实停牌/退市/代码'})
        stocks.append({'code':code,'name':'持仓行情缺失','bars':[]})
    errors.sort(key=lambda e:e['code'])
    if len(stocks)<1000: raise ValueError('市场覆盖异常，不覆盖原缓存')
    result={'mode':'live','as_of':expected,'expected_session':expected,'calendar_verified':True,'calendar':sorted(str(x)[:10] for x in calendar['trade_date']),'complete':not errors,'stocks':stocks,'errors':errors,'fetched_at':now.isoformat(),'adjust':'不复权'}
    result['provider']=provider
    result['coverage']=coverage(result)
    result['complete']=not errors and not coverage_warning(result)
    result['quality_warnings']=[coverage_warning(result)] if coverage_warning(result) else []
    temp=DATA/'live.tmp'; temp.write_text(json.dumps(result,ensure_ascii=False,allow_nan=False),encoding='utf-8'); temp.replace(DATA/'live.json')
    return {'stocks':len(stocks),'errors':len(errors),'as_of':expected,'complete':result['complete'],'coverage':result['coverage'],'warnings':result['quality_warnings'],'requests':len(todo),'incremental':bool(previous) and not full}
