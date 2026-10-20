import asyncio
import unittest
from unittest.mock import patch
import httpx
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import global_markets as g

META={'regularMarketPrice':105,'chartPreviousClose':100,'regularMarketTime':1792184399}

class GlobalMarketTests(unittest.TestCase):
    def setUp(self):
        g._cache.clear();g._last_attempt=None
        self.small=g.BENCHMARKS[:2]

    def test_previous_close_and_invalid_values(self):
        parsed=g.parse_quote(META)
        self.assertEqual(parsed['change'],5)
        self.assertAlmostEqual(parsed['changePct'],5)
        self.assertEqual(parsed['quoteTime'],'2026-10-16T20:59:59+00:00')
        no_previous=g.parse_quote({**META,'chartPreviousClose':None})
        self.assertIsNone(no_previous['changePct'])
        for value in [float('nan'),float('inf'),-1,0]:
            with self.assertRaises(ValueError):g.parse_quote({**META,'regularMarketPrice':value})

    def test_catalog_has_global_indices_and_units(self):
        self.assertEqual(len(g.BENCHMARKS),22)
        self.assertEqual(len({b['symbol'] for b in g.BENCHMARKS}),22)
        by_symbol={b['symbol']:b for b in g.BENCHMARKS}
        self.assertEqual(by_symbol['^SPGSCI']['instrumentType'],'index')
        self.assertEqual(by_symbol['ZC=F']['unit'],'美分/蒲式耳')
        self.assertEqual(by_symbol['GC=F']['instrumentType'],'futures')

    def test_fallback_and_cache_coalescing(self):
        async def run():
            calls=[]
            def handler(req):
                calls.append(req)
                if req.url.host.startswith('query1'):return httpx.Response(503)
                return httpx.Response(200,json={'chart':{'error':None,'result':[{'meta':META}]}})
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                with patch.object(g,'BENCHMARKS',self.small):
                    a,b=await asyncio.gather(g.get_global_markets(client),g.get_global_markets(client))
                    self.assertEqual(a,b)
                    self.assertEqual(len(calls),4)
                    self.assertFalse(a['items'][0]['stale'])
        asyncio.run(run())

    def test_partial_failure_preserves_quote_time(self):
        async def run():
            async with httpx.AsyncClient(transport=httpx.MockTransport(lambda req:httpx.Response(503))) as client:
                with patch.object(g,'BENCHMARKS',self.small):
                    g._cache[self.small[0]['symbol']]={**self.small[0],**g.parse_quote(META)}
                    result=await g.get_global_markets(client)
                    self.assertTrue(result['items'][0]['stale'])
                    self.assertEqual(result['items'][0]['quoteTime'],g.parse_quote(META)['quoteTime'])
                    self.assertTrue(result['items'][1]['unavailable'])
                    self.assertIsNone(result['items'][1]['price'])
        asyncio.run(run())

if __name__=='__main__':unittest.main()
