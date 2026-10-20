import asyncio
import unittest
from unittest.mock import patch
import httpx
from test_regressions import m
from fastapi.testclient import TestClient

SAMPLE='var suggest="招商银行,11,600036,sh600036,招商银行,,招商银行,99,1,,,;招商银行,31,03968,03968,招商银行,,招商银行,99,1,,,;指数,11,000001,sh000001,上证指数,,上证指数,99,1,,,;退市,11,000024,sz000024,退市,,退市,99,0,,,;苹果,41,aapl,aapl,苹果,,苹果,99,1,,,";'

class SearchTests(unittest.TestCase):
    def setUp(self):m._search_cache.clear()

    def test_parser_rejects_delisted_and_misrouted_indices(self):
        rows=m._parse_symbol_suggestions(SAMPLE)
        self.assertEqual([r['symbol'] for r in rows],['600036','03968','AAPL'])

    def test_remote_search_cache_and_distinct_markets(self):
        async def run():
            calls=[]
            def handler(req):
                calls.append(req)
                return httpx.Response(200,content=SAMPLE.encode('gbk'))
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                with patch.object(m,'_client',client),patch.object(m,'_get_watchlist',return_value=[]):
                    first=await m.search_symbols('招商银行')
                    second=await m.search_symbols('招商银行')
            self.assertEqual(len(calls),1)
            self.assertIn('600036',[i['symbol'] for i in first['items']])
            self.assertIn('03968',[i['symbol'] for i in first['items']])
            self.assertEqual(first,second)
        asyncio.run(run())

    def test_partial_local_results_and_outage(self):
        async def run():
            def handler(req):return httpx.Response(503)
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                with patch.object(m,'_client',client),patch.object(m,'_get_watchlist',return_value=[]):
                    result=await m.search_symbols('比特币')
                    self.assertEqual(result['items'][0]['symbol'],'BTCUSDT')
                    self.assertTrue(result['partial'])
                    with self.assertRaises(m.HTTPException) as exc:await m.search_symbols('找不到的名称')
                    self.assertEqual(exc.exception.status_code,502)
        asyncio.run(run())

    def test_aster_and_new_usdt_pair_routing(self):
        self.assertEqual(m._identify_market('ASTER'), 'crypto')
        self.assertEqual(m._identify_market('ASTERUSDT'), 'crypto')
        self.assertEqual(m._identify_market('NEWCOINUSDT'), 'crypto')
        self.assertEqual(m._identify_market('XAUUSDT'), 'metal')
        self.assertEqual(m.kline_info('ASTER')['providerSymbol'], 'ASTERUSDT')
        self.assertEqual(m.kline_info('ASTERUSDT')['source'], 'binance')

    def test_api_validation(self):
        async def no_poll():return []
        with patch.object(m,'_refresh_prices',no_poll),TestClient(m.app) as client:
            self.assertEqual(client.get('/api/search?q=').status_code,422)
            self.assertEqual(client.get('/api/search?q='+('a'*41)).status_code,422)
            self.assertEqual(client.get('/api/search?q=%20').status_code,400)

if __name__=='__main__':unittest.main()
