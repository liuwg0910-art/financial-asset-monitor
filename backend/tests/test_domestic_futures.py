import asyncio
from datetime import datetime, timezone
import json
import unittest
from unittest.mock import patch
import httpx
from test_regressions import m
import domestic_futures as d


class DomesticFuturesTests(unittest.TestCase):
    def test_identity_units_and_no_stock_collisions(self):
        for symbol in ['RB0','RB2701','AU2612','I2701','TA2701','LC0','IF2611','T0']:
            self.assertEqual(m._identify_market(symbol),'futures')
            self.assertEqual(m.kline_info(symbol)['source'],'sina')
            self.assertNotIn('4h',m.kline_info(symbol)['supportedIntervals'])
        for symbol in ['AAPL','SI=F','IF2613','RB00','RB2701/x']:
            self.assertIsNone(d.contract_info(symbol))
        self.assertEqual(d.contract_info('AU0')['unit'],'元/克')
        self.assertEqual(d.contract_info('IF0')['unit'],'点')
        self.assertTrue(d.search_contracts('TA701',datetime(2026,10,20,tzinfo=timezone.utc)))

    def test_commodity_quote_uses_settlement_and_source_timestamp(self):
        parts=['螺纹钢2701','093610','3071','3077','3066','0','3066','3067','3067','0','3080','4265','362','1761995','208500','沪','螺纹钢','2026-10-20']
        text='var hq_str_nf_RB2701="'+','.join(parts)+'";'
        q=d.parse_quote(d.contract_info('RB2701'),text)
        self.assertEqual(q['price'],3067)
        self.assertEqual(q['previousSettlement'],3080)
        self.assertAlmostEqual(q['changePct'],(3067/3080-1)*100)
        self.assertEqual(q['quoteTime'],'2026-10-20T01:36:10+00:00')
        self.assertEqual(q['openInterest'],1761995)
        with self.assertRaises(ValueError):d.parse_quote(d.contract_info('RB0'),text)

    def test_financial_futures_quote_layout(self):
        p=['0']*50
        for i,v in {0:'4127.4',1:'4129.2',2:'4110.8',3:'4113.6',4:'2150',6:'47238',13:'4137.2',36:'2026-10-20',37:'09:36:06',49:'沪深300股指2611'}.items():p[i]=v
        q=d.parse_quote(d.contract_info('IF2611'),'var hq_str_nf_IF2611="'+','.join(p)+'";')
        self.assertEqual(q['price'],4113.6)
        self.assertEqual(q['close'],4137.2)
        self.assertEqual(q['quoteTime'],'2026-10-20T01:36:06+00:00')

    def test_real_bar_jsonp_and_beijing_time(self):
        async def run():
            data=[dict(d='2026-10-19 21:05:00',o='100',h='103',l='99',c='101',v='50')]
            requests=[]
            def handler(r):
                requests.append(r)
                return httpx.Response(200,text='/* comment */\nvar _data=('+json.dumps(data)+');')
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                with patch.object(m,'_client',client):
                    m._kline_cache.clear()
                    bars=await m.fetch_kline('RB2701','5m',30)
                    self.assertEqual(bars[0]['time'],int(datetime(2026,10,19,13,5,tzinfo=timezone.utc).timestamp()))
                    self.assertEqual(bars[0]['volume'],50)
                    self.assertEqual(requests[0].url.params['type'],'5')
                    with self.assertRaises(m.HTTPException):await m.fetch_kline('RB2701','4h',30)
        asyncio.run(run())


if __name__=='__main__':unittest.main()
