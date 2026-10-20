import asyncio
from datetime import date, datetime, timezone
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch, AsyncMock
import httpx
from fastapi.testclient import TestClient

from test_regressions import m
from basis import BasisService, annualized, quarter_pair
from alerts import AlertEngine


class BasisTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.engine=AlertEngine(Path(self.temp.name)/'state.json')
    def tearDown(self):self.temp.cleanup()

    def test_formula_sign_and_quarter_roll(self):
        self.assertAlmostEqual(annualized(100,97.65),-9.4)
        self.assertAlmostEqual(annualized(100,97.45),-10.2)
        self.assertAlmostEqual(annualized(100,101),4)
        self.assertEqual(quarter_pair('IC',date(2026,10,20)),('IC2612','IC2703'))
        self.assertEqual(quarter_pair('IM',date(2026,9,18)),('IM2609','IM2612'))
        self.assertEqual(quarter_pair('IM',date(2026,9,19)),('IM2612','IM2703'))
        self.assertEqual(quarter_pair('IC',date(2026,6,22),{'IC2606':date(2026,6,22)}),('IC2606','IC2609'))
        for a,b in [(0,1),(1,float('nan')),(-1,2)]:
            with self.assertRaises(ValueError):annualized(a,b)

    def test_quote_sync_and_old_quote_suppression(self):
        async def run():
            now=datetime.now(timezone.utc)
            async def quote(client,info):
                from datetime import timedelta
                t=now-timedelta(seconds=180 if info['symbol'].startswith('IM') else 0)
                # Adjacent quarters always differ; keep this test valid in every season.
                if int(info['symbol'][-2:]) in (6,12):t=t-timedelta(seconds=90)
                return {'price':100,'quoteTime':t.isoformat()}
            with patch('basis.domestic.fetch_quote',quote):
                data=await BasisService().current(None)
            self.assertTrue(all(not i['available'] for i in data['items']))
            async def old(client,info):return {'price':100,'quoteTime':(now-timedelta(seconds=180)).isoformat()}
            from datetime import timedelta
            with patch('basis.domestic.fetch_quote',old):
                data=await BasisService().current(None)
            self.assertTrue(all(i['available'] and i['stale'] for i in data['items']))
        asyncio.run(run())

    def test_history_same_day_match_and_no_interpolation(self):
        async def run():
            now=datetime.now(timezone.utc).astimezone(__import__('domestic_futures').CN_TZ).date()
            from datetime import timedelta
            day=now-timedelta(days=5)
            front,far=quarter_pair('IC',day)
            stamp=int(datetime.combine(day,datetime.min.time(),tzinfo=__import__('domestic_futures').CN_TZ).timestamp())
            async def bars(client,symbol,interval):
                if symbol==front:return [dict(time=stamp,close=100),dict(time=stamp+86400,close=100)]
                if symbol==far:return [dict(time=stamp,close=98)]
                return []
            with patch('basis.domestic.fetch_kline',bars):data=await BasisService().history(None)
            points=data['series'][0]['points']
            self.assertAlmostEqual(points[0]['rate'],-8)
            self.assertIsNone(points[1]['rate'])
            self.assertEqual(data['series'][0]['missingDays'],1)
        asyncio.run(run())

    def item(self,rate=-11,**changes):
        return {**dict(symbol='IC',rate=rate,available=True,stale=False,frontSymbol='IC2612',farSymbol='IC2703',
                    frontPrice=100,farPrice=97.25,quoteTime='2026-10-20T01:30:00+00:00'),**changes}

    def test_threshold_latch_cooldown_pause_stale_and_persistence(self):
        rule=self.engine.add_basis_rule(dict(symbol='IC',operator='lte',thresholdPct=-10,cooldownMinutes=1))
        self.assertEqual(self.engine.observe_basis([self.item(stale=True)],now=0),[])
        events=self.engine.observe_basis([self.item()],now=1)
        self.assertEqual(events[0]['kind'],'basis')
        self.assertEqual(self.engine.observe_basis([self.item(-12)],now=100),[])
        self.engine.observe_basis([self.item(-8)],now=101)
        self.assertEqual(len(self.engine.observe_basis([self.item()],now=102)),1)
        self.engine.change_basis_rule(rule['id'],enabled=False)
        self.assertEqual(self.engine.observe_basis([self.item()],now=300),[])
        loaded=AlertEngine(self.engine.path)
        self.assertEqual(len(loaded.public_state()['basisRules']),1)
        loaded.change_basis_rule(rule['id'],delete=True)
        self.assertEqual(loaded.public_state()['basisRules'],[])

    def test_wechat_basis_message_and_api_validation(self):
        self.engine.configure_wechat(True,'SCT1234567890testing')
        self.engine.add_basis_rule(dict(symbol='IC',operator='gte',thresholdPct=-7,cooldownMinutes=1))
        event=self.engine.observe_basis([self.item(-6)],now=1)[0]
        async def run():
            requests=[]
            def handle(request):requests.append(request);return httpx.Response(200,json={'code':0})
            async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
                self.assertEqual((await self.engine.send_wechat(client,event))[0],'sent')
            self.assertIn(b'IC2612',requests[0].content)
        asyncio.run(run())
        async def no_poll():await asyncio.Event().wait()
        with patch.object(m,'alert_engine',self.engine),patch.object(m,'_poll_loop',no_poll),TestClient(m.app) as client:
            self.assertEqual(client.post('/api/alerts/basis-rules',json=dict(symbol='IC',operator='lte',thresholdPct=-10)).status_code,200)
            for body in [dict(symbol='IF',operator='lte',thresholdPct=-10),dict(symbol='IC',operator='lte',thresholdPct=-101)]:
                self.assertEqual(client.post('/api/alerts/basis-rules',json=body).status_code,422)
            self.assertNotIn('SCT1234567890testing',client.get('/api/alerts').text)

    def test_background_monitor_enqueues_basis_alert_without_open_page(self):
        self.engine.add_basis_rule(dict(symbol='IC',operator='lte',thresholdPct=-10,cooldownMinutes=1))
        async def run():
            queue=asyncio.Queue()
            with patch.object(m,'alert_engine',self.engine),patch.object(m,'_alert_queue',queue), \
                 patch.object(m,'_refresh_prices',AsyncMock(return_value=[])), \
                 patch.object(m.manager,'broadcast',AsyncMock()), \
                 patch.object(m.basis_service,'current',AsyncMock(return_value={'items':[self.item()]})), \
                 patch('main.asyncio.sleep',AsyncMock(side_effect=asyncio.CancelledError)):
                with self.assertRaises(asyncio.CancelledError):await m._poll_loop()
                event=queue.get_nowait()
                self.assertEqual(event['kind'],'basis')
                self.assertEqual(event['rate'],-11)
        asyncio.run(run())

    def test_outage_keeps_persisted_quote_as_stale_and_recovers(self):
        async def run():
            path=Path(self.temp.name)/'snapshot.json'
            async def good(client,info):return {'price':100,'quoteTime':datetime.now(timezone.utc).isoformat()}
            with patch('basis.domestic.fetch_quote',good):
                first=await BasisService(path).current(None)
            with patch('basis.domestic.fetch_quote',AsyncMock(return_value=None)):
                restored=BasisService(path)
                failed=await restored.current(None)
            self.assertTrue(all(i['cached'] and i['stale'] for i in failed['items']))
            self.assertEqual(failed['items'][0]['rate'],first['items'][0]['rate'])
            self.engine.add_basis_rule(dict(symbol='IC',operator='gte',thresholdPct=-1,cooldownMinutes=1))
            self.assertEqual(self.engine.observe_basis(failed['items']),[])
            restored.current_cache=(restored.current_cache[0],0,failed)
            with patch('basis.domestic.fetch_quote',good):
                recovered=await restored.current(None)
            self.assertTrue(all(not i.get('cached') and not i['stale'] for i in recovered['items']))
        asyncio.run(run())

    def test_empty_history_is_retried_and_last_good_history_retained(self):
        async def run():
            service=BasisService()
            day=datetime.now(timezone.utc).date()-__import__('datetime').timedelta(days=5)
            stamp=int(datetime.combine(day,datetime.min.time(),tzinfo=__import__('domestic_futures').CN_TZ).timestamp())
            async def good(client,symbol,interval):return [dict(time=stamp,close=100)]
            with patch('basis.domestic.fetch_kline',good):first=await service.history(None)
            service.history_cache=None
            with patch('basis.domestic.fetch_kline',AsyncMock(side_effect=httpx.ConnectError('offline'))):
                failed=await service.history(None)
            self.assertTrue(failed['cached'])
            self.assertEqual(failed['retrySeconds'],15)
            self.assertEqual(failed['series'],first['series'])
            empty=BasisService()
            with patch('basis.domestic.fetch_kline',AsyncMock(side_effect=httpx.ConnectError('offline'))):
                failed=await empty.history(None)
            self.assertEqual(failed['retrySeconds'],15)
            empty.history_cache=(empty.history_cache[0],0,failed)
            with patch('basis.domestic.fetch_kline',good):recovered=await empty.history(None)
            self.assertTrue(any(p['rate'] is not None for s in recovered['series'] for p in s['points']))
        asyncio.run(run())


if __name__=='__main__':unittest.main()
