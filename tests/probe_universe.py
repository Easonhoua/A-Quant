"""Read-only stock-name coverage probe, bounded to 120 seconds."""
import subprocess
import sys

if __name__ == '__main__':
    source = """
import akshare as ak
from backend.engine import allowed
f=ak.stock_zh_a_spot()
codes=f['代码'].astype(str).str.replace(r'^(sh|sz|bj)', '', regex=True)
assert not codes.duplicated().any(), 'duplicate stock codes'
eligible=sum(allowed(c,n) for c,n in zip(codes,f['名称']))
assert eligible>=1000, 'insufficient coverage'
print('universe PASS total',len(f),'eligible',eligible)
print(f[['代码','名称']].head(3).to_string(index=False))
"""
    try:
        result=subprocess.run([sys.executable,'-X','utf8','-c',source],capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=120)
        print(result.stdout.strip())
        if result.returncode:
            print('universe FAIL',result.stderr[-1200:]);sys.exit(1)
    except subprocess.TimeoutExpired:
        print('universe TIMEOUT (120s)');sys.exit(1)
