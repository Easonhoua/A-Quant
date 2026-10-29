import argparse, json
from .data import sync_live, load, DATA
from .engine import daily
from .store import Store

def main():
    p=argparse.ArgumentParser(description='A-Quant 收盘同步和日报（持仓优先）')
    p.add_argument('command',choices=['demo','sync','report','daily','repair'])
    p.add_argument('--mode',choices=['demo','live'],default='demo')
    p.add_argument('--capital',type=float,default=100000)
    p.add_argument('--provider',choices=['eastmoney','tencent'],default='eastmoney')
    p.add_argument('--adx-filter',action='store_true',help='S1启用ADX对照过滤')
    p.add_argument('--code',action='append',help='repair局部补取股票代码，可重复传入')
    a=p.parse_args()
    try:
        sync_error=None
        if a.command=='repair':
            if not a.code or any(len(c)!=6 or not c.isdigit() for c in a.code): raise ValueError('repair需要六位--code')
            print(json.dumps(sync_live(provider=a.provider,codes=a.code),ensure_ascii=False));return
        if a.command in ('sync','daily'):
            try: print(json.dumps(sync_live(provider=a.provider),ensure_ascii=False))
            except Exception as e:
                if a.command=='sync':raise
                sync_error=str(e)
        if a.command in ('demo','report','daily'):
            mode='live' if a.command=='daily' else a.mode
            if a.capital<10000: raise ValueError('总权益至少10000')
            try:snapshot=load(mode)
            except (ValueError,OSError) as e:
                snapshot={'mode':mode,'as_of':'','calendar_verified':False,'complete':False,'stocks':[],'errors':[{'error':str(e)}]}
            if sync_error:
                snapshot['complete']=False
                snapshot.setdefault('errors',[]).append({'error':'本次同步失败：'+sync_error})
            result=daily(snapshot,Store().positions(mode),a.capital,a.adx_filter)
            path=DATA/f'report-{mode}.json';path.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
            print(str(path));print(' / '.join(result['warnings']) or '日报生成完成')
            if sync_error: print('本次同步失败，已生成持仓复核/缺失提示日报：'+sync_error);raise SystemExit(1)
    except Exception as e:
        print(f'失败：{e}；未切换为示例数据');raise SystemExit(1)

if __name__=='__main__':main()
