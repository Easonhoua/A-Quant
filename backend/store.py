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
            db.execute('CREATE TABLE IF NOT EXISTS position_resets (mode TEXT, cutoff_id INTEGER, PRIMARY KEY(mode, cutoff_id))')
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
        cutoff=db.execute('SELECT COALESCE(MAX(cutoff_id),0) FROM position_resets WHERE mode=?',(mode,)).fetchone()[0]
        for code,side,q,price,day in db.execute('SELECT code,side,quantity,price,trade_date FROM trades WHERE mode=? AND id>? ORDER BY trade_date,id',(mode,cutoff)):
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
    def clear_positions(self,mode):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            count=len(self.positions(mode,db))
            cutoff=db.execute('SELECT COALESCE(MAX(id),0) FROM trades WHERE mode=?',(mode,)).fetchone()[0]
            db.execute('INSERT OR IGNORE INTO position_resets(mode,cutoff_id) VALUES (?,?)',(mode,cutoff))
        return {'status':'cleared','cleared_positions':count}

    def ledger(self,mode):
        with self.connect() as db:
            db.row_factory=sqlite3.Row
            rows=[dict(r) for r in db.execute('SELECT * FROM trades WHERE mode=? ORDER BY trade_date,id',(mode,))]
            resets={r[0] for r in db.execute('SELECT cutoff_id FROM position_resets WHERE mode=?',(mode,))}
        positions={}
        for row in rows:
            q,cost=positions.get(row['code'],(0,0))
            if row['side']=='BUY':
                positions[row['code']]=(q+row['quantity'],(q*cost+row['quantity']*row['price'])/(q+row['quantity']))
                row['realized_pnl']=None
            else:
                row['realized_pnl']=round((row['price']-cost)*row['quantity'],2)
                remaining=q-row['quantity']
                positions[row['code']]=(remaining,cost if remaining else 0)
            if row['id'] in resets: positions.clear()
        return list(reversed(rows))
