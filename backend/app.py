from datetime import date
from typing import Literal
from fastapi import FastAPI, HTTPException, Query
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from .data import load, ROOT
from .engine import daily, backtest, STRATEGY
from .store import Store

app=FastAPI(title='A-Quant V1',version='1.0.0')
store=Store()

class Trade(BaseModel):
    code: str = Field(pattern=r'^\d{6}$')
    side: Literal['BUY','SELL']
    quantity: int = Field(gt=0, le=100000000, strict=True)
    price: float = Field(gt=0, le=1000000, allow_inf_nan=False)
    trade_date: date
    mode: Literal['demo','live']='demo'
    request_id: str = Field(min_length=8,max_length=80)

class TestRequest(BaseModel):
    code: str = Field(pattern=r'^\d{6}$')
    model: Literal['S1','S2']='S1'
    mode: Literal['demo','live']='demo'
    capital: float = Field(default=100000,ge=10000,le=1e10,allow_inf_nan=False)
    commission: float = Field(default=.0003,ge=0,le=.01,allow_inf_nan=False)
    tax: float = Field(default=.0005,ge=0,le=.01,allow_inf_nan=False)
    adx_filter: bool = False
    strict_filter: bool = True
    slippage: float = Field(default=.001,ge=0,le=.05,allow_inf_nan=False)

@app.get('/api/health')
def health(): return {'status':'ok','version':'1.0.0','strategy_version':STRATEGY['version'],'trading':'manual-only'}

@app.get('/api/daily')
def report(mode:Literal['demo','live']='demo',capital:float=Query(100000,ge=10000,le=1e10,allow_inf_nan=False),
           adx_filter:bool=False,strict_filter:bool=True,amplitude_filter:bool=True,market_filter:bool=True,
           top_n:int=Query(3,ge=1,le=20),model_filter:Literal['ALL','S1','S2']='ALL'):
    try:
        try: snapshot=load(mode)
        except (ValueError,OSError) as e:
            snapshot={'mode':mode,'as_of':'','calendar_verified':False,'complete':False,'stocks':[],'errors':[{'error':str(e)}]}
        result=daily(snapshot,store.positions(mode),capital,adx_filter,strict_filter,
                     amplitude_filter=amplitude_filter,market_filter=market_filter,top_n=top_n,
                     model_filter=model_filter)
        result['ledger']=store.ledger(mode)
        return result
    except (ValueError,KeyError) as e: raise HTTPException(409,str(e))

@app.post('/api/trades')
def trade(t:Trade):
    try:
        data=t.model_dump();data['trade_date']=t.trade_date.isoformat()
        return store.record(data)
    except ValueError as e: raise HTTPException(400,str(e))

@app.post('/api/backtest')
def test(t:TestRequest):
    try:
        s=next((s for s in load(t.mode)['stocks'] if s['code']==t.code),None)
        if not s: raise ValueError('股票不在行情数据中')
        return backtest(s,t.model,t.capital,t.commission,t.tax,t.slippage,t.adx_filter,t.strict_filter)
    except ValueError as e: raise HTTPException(400,str(e))

if (ROOT/'frontend'/'dist').exists():
    app.mount('/',StaticFiles(directory=ROOT/'frontend'/'dist',html=True),name='ui')
