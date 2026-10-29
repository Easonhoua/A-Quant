import sqlite3
from contextlib import contextmanager
from datetime import date,datetime
from zoneinfo import ZoneInfo
from .data import DATA

class Store:
    def __init__(self, path=None):
        self.path=path or DATA/'state.sqlite3'
        with self.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS trades (id INTEGER PRIMARY KEY, code TEXT, side TEXT, quantity INTEGER, price REAL, trade_date TEXT, mode TEXT, request_id TEXT UNIQUE)')
    @contextmanager
    def connect(self):
        db=sqlite3.connect(self.path, timeout=10)
        try:
            with db: yield db
        finally:
            db.close()
    def positions(self, mode='demo', db=None):
        if db is None:
            with self.connect() as conn: return self.positions(mode,conn)
        result={}
        for code,side,q,price,day in db.execute('SELECT code,side,quantity,price,trade_date FROM trades WHERE mode=? ORDER BY trade_date,id',(mode,)):
            p=result.setdefault(code,{'code':code,'quantity':0,'avg_cost':0,'lots':[]})
            if side=='BUY':
                p['avg_cost']=(p['avg_cost']*p['quantity']+q*price)/(p['quantity']+q); p['quantity']+=q; p['lots'].append([day,q])
            else:
                p['quantity']-=q
                left=q
                for lot in p['lots']:
                    n=min(left,lot[1]);lot[1]-=n;left-=n
                if not p['quantity']:p['avg_cost']=0
        return [{k:v for k,v in p.items() if k!='lots'} for p in result.values() if p['quantity']>0]
    def record(self, t):
        # BEGIN IMMEDIATE serializes validate+append to prevent concurrent oversells.
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            old=db.execute('SELECT code,side,quantity,price,trade_date,mode FROM trades WHERE request_id=?',(t['request_id'],)).fetchone()
            values=tuple(t[k] for k in ('code','side','quantity','price','trade_date','mode'))
            if old:
                if old!=values: raise ValueError('重复请求编号内容不一致')
                return {'status':'already_recorded'}
            latest=db.execute('SELECT MAX(trade_date) FROM trades WHERE mode=?',(t['mode'],)).fetchone()[0]
            if latest and t['trade_date']<latest: raise ValueError('请按成交日期顺序报告；历史更正需核对账本')
            if date.fromisoformat(t['trade_date'])>datetime.now(ZoneInfo('Asia/Shanghai')).date(): raise ValueError('成交日期不能在未来')
            p=next((p for p in self.positions(t['mode'],db) if p['code']==t['code']),None)
            if t['side']=='SELL' and (not p or t['quantity']>p['quantity']): raise ValueError('卖出数量超过已报告持仓')
            db.execute('INSERT INTO trades(code,side,quantity,price,trade_date,mode,request_id) VALUES (?,?,?,?,?,?,?)', values+(t['request_id'],))
        return {'status':'recorded'}
    def ledger(self,mode):
        with self.connect() as db:
            db.row_factory=sqlite3.Row
            return [dict(r) for r in db.execute('SELECT * FROM trades WHERE mode=? ORDER BY trade_date,id',(mode,))]
