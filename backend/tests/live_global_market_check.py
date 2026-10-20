"""Live integration check for the independent global benchmark API."""
import json,sys
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import main as m
from fastapi.testclient import TestClient
async def no_poll():return []
with patch.object(m,'_refresh_prices',no_poll),TestClient(m.app) as client:
    before=client.get('/api/custom').json()
    response=client.get('/api/global-markets')
    assert response.status_code==200,response.text
    payload=response.json()
    assert len(payload['items'])==22
    failures=[]
    for row in payload['items']:
        if row['unavailable'] or row['stale'] or row['price'] is None or row['changePct'] is None:failures.append(row['symbol'])
        print(row['symbol'],'PASS' if row['symbol'] not in failures else 'FAIL',row['price'],flush=True)
    assert client.get('/api/custom').json()==before,'Watchlist changed'
    out=Path(__file__).resolve().parents[2]/'docs'/'global_market_live_results.json'
    out.write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding='utf-8')
    assert not failures,failures
    print('22/22 live quotes PASS; watchlist unchanged',flush=True)
