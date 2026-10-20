"""Online name-search/add smoke test using an isolated temporary watchlist."""
import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import main as m
from fastapi.testclient import TestClient

cases=[('宁德时代','300750'),('苹果','AAPL'),('黄金','XAUUSD'),('比特币','BTCUSDT')]
results=[]
with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as tmp:
    watchlist=Path(tmp)/'watchlist.json'
    watchlist.write_text('[]',encoding='utf-8')
    with patch.object(m,'WATCHLIST_FILE',watchlist),TestClient(m.app) as client:
        for query,symbol in cases:
            response=client.get('/api/search',params={'q':query})
            assert response.status_code==200,response.text
            items=response.json()['items']
            selected=next(i for i in items if i['symbol']==symbol)
            added=client.post('/api/custom',json={'symbol':selected['symbol'],'name':selected['name']})
            assert added.status_code==200,added.text
            assert any(i['symbol']==symbol for i in client.get('/api/custom').json())
            results.append({'query':query,'symbol':symbol,'name':selected['name'],'matches':len(items),'status':'PASS'})
            print(symbol,'name search + real quote validation + saved watchlist PASS',flush=True)
            assert client.delete('/api/custom/'+symbol).status_code==200
        assert client.get('/api/custom').json()==[]
out=Path(__file__).resolve().parents[2]/'docs'/'name_search_live_results.json'
out.write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf-8')
