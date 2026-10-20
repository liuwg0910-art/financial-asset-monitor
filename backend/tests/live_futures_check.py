"""Read-only live check; never changes watchlist or sends notifications."""
import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import httpx
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import main as m


async def run():
    results=[]
    async with httpx.AsyncClient(timeout=20,follow_redirects=True,headers={'User-Agent':'Mozilla/5.0'}) as client:
        m._client=client
        for symbol in ['BZ=F','CL=F','GC=F','GCZ26.CMX','BZZ26.NYM']:
            row={'symbol':symbol}
            try:
                quote=await m.fetch_price(symbol)
                assert quote and quote['price']>0 and quote['market']=='futures'
                row.update(price=quote['price'],quoteTime=quote['quoteTime'],contract=quote['providerContract'])
                for interval in ['1h','1d']:
                    bars=await m.fetch_kline(symbol,interval,30)
                    assert len(bars)>1 and bars==m._clean_candles(bars)
                    row[interval]=len(bars)
                row['status']='PASS'
            except Exception as exc:
                row.update(status='FAIL',error=str(getattr(exc,'detail',exc)))
            results.append(row)
            print(json.dumps(row,ensure_ascii=False),flush=True)
        m._client=None
    target=Path(__file__).resolve().parents[2]/'docs'/'futures_live_results.json'
    target.write_text(json.dumps({'checkedAt':datetime.now(timezone.utc).isoformat(),'results':results},ensure_ascii=False,indent=2),encoding='utf-8')
    return any(r['status']!='PASS' for r in results)


if __name__=='__main__':sys.exit(asyncio.run(run()))
