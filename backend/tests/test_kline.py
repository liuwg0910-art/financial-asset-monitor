import asyncio
import unittest
from unittest.mock import patch
import httpx
from test_regressions import m
from fastapi.testclient import TestClient


def bar(t, o=100, h=110, l=90, c=105, v=10):
    return dict(time=t,open=o,high=h,low=l,close=c,volume=v)


class KlineTests(unittest.TestCase):
    def setUp(self):
        m._kline_cache.clear()
        m._kline_locks.clear()

    def test_market_mapping(self):
        expected = {'600036':'600036.SS','000001':'000001.SZ','00700':'0700.HK',
                    'AAPL':'AAPL','HSI':'^HSI','HSCEI':'^HSCE','HSTECH':'hkHSTECH',
                    'XAUUSD':'GC=F','GOLD':'GC=F','XAGUSDT':'SI=F'}
        for symbol, provider in expected.items():
            self.assertEqual(m.kline_info(symbol)['providerSymbol'],provider)
        self.assertEqual(m.kline_info('HSTECH')['supportedIntervals'],['1d'])

    def test_invalid_and_duplicate_candles(self):
        rows=[bar(200),bar(100),bar(100,c=103),bar(300,o=None),bar(400,h=99),bar(500,v=-1),bar(600,c=float('nan'))]
        data=m._clean_candles(rows)
        self.assertEqual([r['time'] for r in data],[100,200])
        self.assertEqual(data[0]['close'],103)

    def test_four_hour_ohlcv(self):
        rows=[bar(14400,c=102),bar(18000,o=102,h=120,l=95,c=115,v=20),bar(28800,c=101)]
        data=m._aggregate_four_hours(rows)
        self.assertEqual(data[0],bar(14400,h=120,c=115,v=30))
        self.assertEqual(len(data),2)

    def test_yahoo_nulls_and_fallback(self):
        calls=[]
        def handler(req):
            calls.append(str(req.url))
            if req.url.host.startswith('query1'):
                return httpx.Response(503)
            return httpx.Response(200,json={'chart':{'error':None,'result':[{
                'timestamp':[14400,18000,21600], 'indicators':{'quote':[{
                    'open':[100,None,102],'high':[110,None,112], 'low':[90,None,92],
                    'close':[105,None,107],'volume':[10,None,20]}]}}]}})
        async def run():
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                with patch.object(m,'_client',client):
                    a,b=await asyncio.gather(m.fetch_kline('XAUUSD','4h',2),m.fetch_kline('XAUUSD','4h',2))
                    self.assertEqual(a,b)
                    return a
        data=asyncio.run(run())
        self.assertEqual(data,[bar(14400,h=112,c=107,v=30)])
        self.assertEqual(len(calls),2)
        self.assertIn('interval=60m',calls[-1])

    def test_tencent_real_daily_shape(self):
        async def run():
            async with httpx.AsyncClient(transport=httpx.MockTransport(lambda req:httpx.Response(200,json={
                'code':0,'data':{'hkHSTECH':{'day':[['2026-10-16','100','105','110','90','1000']]}}}))) as client:
                with patch.object(m,'_client',client):
                    return await m.fetch_kline('HSTECH','1d',2)
        self.assertEqual(asyncio.run(run())[0]['close'],105)

    def test_api_metadata_and_upstream_errors(self):
        async def no_poll():
            return []
        async def yahoo(*args):
            return [bar(14400),bar(28800)]
        with patch.object(m,'_refresh_prices',no_poll),patch.object(m,'_fetch_kline_yahoo',yahoo),TestClient(m.app) as client:
            response=client.get('/api/kline/XAUUSD?interval=4h&limit=2')
            self.assertEqual(response.status_code,200)
            self.assertEqual(response.json()['providerSymbol'],'GC=F')
            self.assertIn('期货',response.json()['notice'])
        m._kline_cache.clear()
        async def fail(*args):
            raise httpx.ConnectError('offline')
        with patch.object(m,'_refresh_prices',no_poll),patch.object(m,'_fetch_kline_yahoo',fail),TestClient(m.app) as client:
            self.assertEqual(client.get('/api/kline/AAPL').status_code,502)
        async def empty(*args):return []
        with patch.object(m,'_refresh_prices',no_poll),patch.object(m,'_fetch_kline_yahoo',empty),TestClient(m.app) as client:
            self.assertEqual(client.get('/api/kline/AAPL').status_code,404)

if __name__=='__main__':unittest.main()
