import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import main as m
import httpx
from fastapi.testclient import TestClient


class RegressionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent)
        self.path = Path(self.temp.name) / 'watchlist.json'
        self.path.write_text('[]', encoding='utf-8')
        self.original = m.WATCHLIST_FILE
        m.WATCHLIST_FILE = self.path
        m._last_prices.clear()
        m._price_times.clear()
        m._kline_cache.clear()
        m._kline_locks.clear()

    def tearDown(self):
        m.WATCHLIST_FILE = self.original
        self.temp.cleanup()

    def test_alias_keeps_requested_symbol(self):
        with patch.object(m, '_fetch_sina', AsyncMock(return_value={'symbol': 'hf_GC', 'price': 2700})) as fetch:
            result = asyncio.run(m.fetch_price('XAUUSD'))
            self.assertEqual(result['symbol'], 'XAUUSD')
            fetch.assert_awaited_once_with('hf_GC')

    def test_binance_without_prev_close_field(self):
        data = dict(lastPrice='110', priceChange='10', priceChangePercent='10', highPrice='115', lowPrice='95', openPrice='100')
        async def run():
            async with httpx.AsyncClient(transport=httpx.MockTransport(lambda req: httpx.Response(200, json=data))) as client:
                with patch.object(m, '_client', client):
                    return await m._fetch_binance('BTCUSDT')
        self.assertEqual(asyncio.run(run())['close'], 100)

    def test_hk_and_us_fields(self):
        hk = 'var hq_str_rt_hk00700="TENCENT,腾讯,100,90,120,95,110,20,22,0,0,0,0";'
        self.assertEqual(m._parse_sina('00700', hk)['price'], 110)
        self.assertEqual(m._parse_sina('00700', hk)['close'], 90)
        us = 'var hq_str_gb_aapl="Apple,110,10,date,5,100,115,99,0,0,0,0";'
        self.assertEqual(m._parse_sina('AAPL', us)['change'], 5)

    def test_cache_coalesces_concurrent_requests(self):
        m._save_watchlist([{'symbol':'BTCUSDT'}])
        async def run():
            with patch.object(m, 'fetch_prices', AsyncMock(return_value=[{'symbol':'BTCUSDT','price':100}])) as fetch:
                await asyncio.gather(m._refresh_prices(), m._refresh_prices())
                self.assertEqual(fetch.await_count, 1)
        asyncio.run(run())

    def test_api_validation_and_persistence(self):
        async def fake(symbol):
            return {'symbol':symbol, 'price':100, 'market':'crypto', 'name':symbol}
        with patch.object(m, 'fetch_price', fake), TestClient(m.app) as client:
            self.assertEqual(client.get('/api/health').status_code, 200)
            self.assertEqual(client.post('/api/custom', json={'symbol':123}).status_code, 400)
            self.assertEqual(client.post('/api/custom', json={'symbol':"<script>"}).status_code, 400)
            self.assertEqual(client.post('/api/custom', json={'symbol':'BTCUSDT'}).status_code, 200)
            self.assertEqual(client.post('/api/custom', json={'symbol':'BTCUSDT'}).status_code, 409)
            self.assertEqual(json.loads(self.path.read_text())[0]['symbol'], 'BTCUSDT')
            self.assertEqual(client.get('/api/kline/BTCUSDT?limit=1001').status_code, 422)
            self.assertEqual(client.get('/api/kline/BTCUSDT?interval=oops').status_code, 422)
            self.assertEqual(client.get('/api/kline/HSTECH?interval=1h').status_code, 422)
            self.assertEqual(client.delete('/api/custom/BTCUSDT').status_code, 200)
            self.assertEqual(client.get('/api/custom').json(), [])
            with client.websocket_connect('/ws') as ws:
                ws.send_json([])
                ws.send_json({'action':'ping'})
                self.assertEqual(ws.receive_json()['type'], 'pong')

if __name__ == '__main__':
    unittest.main()

