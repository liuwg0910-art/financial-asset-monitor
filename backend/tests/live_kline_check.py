"""Opt-in integration check: calls public sources; does not change watchlist."""
import asyncio
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
import httpx
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import main as m
from fastapi import HTTPException

async def run():
    cases=[(s,i) for s in ['600036','00700','AAPL','XAUUSD','BTCUSDT'] for i in m.kline_info(s)['supportedIntervals']]
    cases += [('HSI','1h'),('HSCEI','1d'),('HSTECH','1d'),('XAGUSD','1d')]
    semaphore=asyncio.Semaphore(4)
    results=[]
    async with httpx.AsyncClient(timeout=20,follow_redirects=True,headers={'User-Agent':'Mozilla/5.0','Referer':'https://finance.yahoo.com/'}) as client:
        m._client=client
        async def check(symbol,interval):
            async with semaphore:
                try:
                    rows=await m.fetch_kline(symbol,interval,150)
                    assert len(rows)>=2
                    assert rows==m._clean_candles(rows)
                    assert all(rows[n]['time']<rows[n+1]['time'] for n in range(len(rows)-1))
                    entry=dict(symbol=symbol,interval=interval,status='PASS',count=len(rows),
                               first=datetime.fromtimestamp(rows[0]['time'],timezone.utc).isoformat(),
                               last=datetime.fromtimestamp(rows[-1]['time'],timezone.utc).isoformat(),
                               lastClose=rows[-1]['close'],**m.kline_info(symbol))
                except Exception as exc:
                    entry=dict(symbol=symbol,interval=interval,status='FAIL',error=exc.detail if isinstance(exc,HTTPException) else str(exc))
                results.append(entry)
                print(symbol,interval,entry['status'],entry.get('count',entry.get('error')),flush=True)
        await asyncio.gather(*(check(*case) for case in cases))
    m._client=None
    results.sort(key=lambda r:(r['symbol'],r['interval']))
    out=Path(__file__).resolve().parents[2]/'docs'/'kline_live_results.json'
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps({'checkedAt':datetime.now(timezone.utc).isoformat(),'results':results},ensure_ascii=False,indent=2),encoding='utf-8')
    failed=sum(r['status']!='PASS' for r in results)
    print(f'{len(results)-failed}/{len(results)} passed',flush=True)
    return failed

if __name__=='__main__':
    logging.disable(logging.WARNING)
    sys.exit(bool(asyncio.run(run())))
