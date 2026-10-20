"""Read-only IC/IM live validation; does not create alert rules or send messages."""
import sys,asyncio,json
from pathlib import Path
from datetime import datetime,timezone
import httpx
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from basis import BasisService,annualized
async def run():
 service=BasisService()
 async with httpx.AsyncClient(timeout=20,headers={'Referer':'https://finance.sina.com.cn','User-Agent':'Mozilla/5.0'}) as client:
  current=await service.current(client)
  history=await service.history(client)
 for item in current['items']:
  assert item['available'],item
  assert abs(annualized(item['frontPrice'],item['farPrice'])-item['rate'])<1e-9
  print(item['symbol'],item['frontSymbol'],item['farSymbol'],round(item['rate'],4),item['quoteTime'])
 for series in history['series']:
  valid=[p for p in series['points'] if p['rate'] is not None]
  assert len(valid)>100,(series['symbol'],len(valid))
  assert all(abs(annualized(p['frontPrice'],p['farPrice'])-p['rate'])<1e-9 for p in valid)
  assert all(series['points'][i]['date']<series['points'][i+1]['date'] for i in range(len(series['points'])-1))
  print(series['symbol'],'valid daily points',len(valid),'missing',series['missingDays'])
 target=Path(__file__).resolve().parents[2]/'docs'/'basis_live_results.json'
 target.write_text(json.dumps(dict(checkedAt=datetime.now(timezone.utc).isoformat(),current=current,history=history),ensure_ascii=False,indent=2),encoding='utf-8')
 print('PASS: quote legs, timestamps, signed formula and historical alignment')
if __name__=='__main__':asyncio.run(run())
