import asyncio
from datetime import datetime, timezone
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch, AsyncMock

import httpx
from fastapi.testclient import TestClient
from test_regressions import m
from futures import contract_info, search_contracts, parse_futures_quote, fetch_futures_quote
from alerts import AlertEngine


class FuturesTests(unittest.TestCase):
    def test_contract_identity_and_search(self):
        for symbol in ['BZ=F', 'CLZ26.NYM', 'GCZ26.CMX', 'ZCH27.CBT']:
            self.assertEqual(m._identify_market(symbol), 'futures')
            self.assertEqual(m.kline_info(symbol)['providerSymbol'], symbol)
        for symbol in ['GCZ26.NYM', 'GCX26.CMX/evil', 'AAPL=F', 'CLZ26.NYM?']:
            self.assertIsNone(contract_info(symbol))
        self.assertEqual(m._identify_market('AAPL'), 'us-stock')
        rows=search_contracts('布伦特',datetime(2026,10,19,tzinfo=timezone.utc))
        self.assertEqual(rows[0]['symbol'],'BZ=F')
        self.assertIn('BZZ26.NYM',[r['symbol'] for r in rows])
        self.assertEqual(contract_info('GCZ26.CMX')['contractMonth'],'2026-12')

    def test_quote_fallback_units_and_provider_time(self):
        meta=dict(symbol='GCZ26.CMX',regularMarketPrice=4200,regularMarketTime=1792100000,
                  chartPreviousClose=4000,regularMarketDayHigh=4250,regularMarketDayLow=4100)
        async def run():
            calls=[]
            def handler(req):
                calls.append(req.url.host)
                return httpx.Response(503) if len(calls)==1 else httpx.Response(200,json={'chart':{'result':[{'meta':meta}]}})
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                quote=await fetch_futures_quote(client,contract_info('GCZ26.CMX'))
            self.assertEqual(len(calls),2)
            self.assertEqual(quote['changePct'],5)
            self.assertEqual(quote['unit'],'美元/盎司')
            self.assertEqual(quote['updated'],quote['quoteTime'])
        asyncio.run(run())
        with self.assertRaises(ValueError):
            parse_futures_quote(contract_info('BZ=F'),{**meta,'regularMarketPrice':float('nan')})

    def test_reject_upstream_contract_substitution(self):
        async def run():
            async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r:httpx.Response(200,json={
                'chart':{'result':[{'meta':dict(symbol='GC=F',regularMarketPrice=100,regularMarketTime=10)}]}}))) as client:
                self.assertIsNone(await fetch_futures_quote(client,contract_info('GCZ26.CMX')))
        asyncio.run(run())

    def test_search_keeps_nearest_delivery_months_first(self):
        async def run():
            async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r:httpx.Response(503))) as client:
                with patch.object(m,'_client',client),patch.object(m,'_get_watchlist',return_value=[]):
                    result=await m.search_symbols('布伦特')
            months=[r['contractMonth'] for r in result['items'] if r.get('contractMonth')]
            self.assertEqual(months,sorted(months))
            self.assertEqual(result['items'][0]['symbol'],'BZ=F')
        asyncio.run(run())

    def test_add_persist_duplicate_and_delete(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'watchlist.json'
            with patch.object(m,'WATCHLIST_FILE',path), patch.object(m,'_refresh_prices',AsyncMock(return_value=[])), patch.object(m,'fetch_price',AsyncMock(return_value={
                **contract_info('GCZ26.CMX'),'price':4200})), TestClient(m.app) as client:
                result=client.post('/api/custom',json={'symbol':'GCZ26.CMX'})
                self.assertEqual(result.status_code,200,result.text)
                self.assertEqual(result.json()['entry']['contractMonth'],'2026-12')
                self.assertEqual(client.get('/api/custom').json()[0]['market'],'futures')
                self.assertEqual(client.post('/api/custom',json={'symbol':'GCZ26.CMX'}).status_code,409)
                self.assertEqual(client.delete('/api/custom/GCZ26.CMX').status_code,200)

    def test_futures_alert_dedup_and_rollover(self):
        with tempfile.TemporaryDirectory() as directory:
            engine=AlertEngine(Path(directory)/'alerts.json')
            engine.add_rule(dict(symbol='BZ=F',name='布伦特',enabled=True,windowMinutes=1,
                                 thresholdPct=1,direction='both',cooldownMinutes=1),now=0)
            def tick(t,price,stamp,contract='Nov'):
                return engine.observe([dict(symbol='BZ=F',market='futures',price=price,
                                            quoteTime=str(stamp),providerContract=contract)],now=t)
            tick(0,100,1)
            for t in [15,30,45]:tick(t,100,1)
            self.assertEqual(len(engine.samples['BZ=F']),1)
            # Sparse data after a closed session must start a new window.
            self.assertEqual(tick(120,110,2),[])
            for t in [135,150,165]:tick(t,110,t)
            self.assertEqual(len(tick(180,112,180)),1)
            self.assertEqual(tick(195,130,195,'Dec'),[])
            self.assertEqual(len(engine.samples['BZ=F']),1)


if __name__=='__main__':unittest.main()
