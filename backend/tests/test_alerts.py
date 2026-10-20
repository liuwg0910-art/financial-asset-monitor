import asyncio
from collections import deque
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import AsyncMock,patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from alerts import AlertEngine
import main as m
import httpx
from fastapi.testclient import TestClient

class AlertTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent)
        self.engine=AlertEngine(Path(self.temp.name)/'alerts.json')
    def tearDown(self):self.temp.cleanup()
    def rule(self,**changes):
        return self.engine.add_rule({'symbol':'BTCUSDT','name':'比特币','windowMinutes':1,'thresholdPct':1,'direction':'both','cooldownMinutes':1,'enabled':True,**changes},now=0)
    def tick(self,t,p):return self.engine.observe([{'symbol':'BTCUSDT','price':p}],now=t)
    def test_warmup_trigger_latch_cooldown_and_rearm(self):
        self.rule(direction='up')
        for t in [0,15,30,45]:self.assertEqual(self.tick(t,100),[])
        self.assertEqual(self.engine.public_state(now=45)['rules'][0]['status'],'warming')
        event=self.tick(60,102)[0]
        self.assertEqual(event['changePct'],2)
        self.assertEqual(event['actualSeconds'],60)
        self.assertEqual(self.tick(75,103),[])
        self.assertEqual(self.tick(90,100),[])
        self.assertEqual(self.tick(105,103),[])
        self.assertEqual(self.tick(120,100),[])
        self.assertEqual(len(self.tick(135,106)),1)
        self.assertEqual(len(self.engine.state['events']),2)
    def test_rolling_baseline_not_daily_change_and_down_direction(self):
        self.rule(direction='down')
        for t in [0,15,30,45]:self.tick(t,100)
        event=self.engine.observe([{'symbol':'BTCUSDT','price':98,'changePct':99}],now=60)[0]
        self.assertEqual(event['changePct'],-2)
        self.assertEqual(event['direction'],'down')
    def test_outage_and_restart_require_new_samples(self):
        self.rule()
        for t in [0,15,30,45]:self.tick(t,100)
        self.assertEqual(self.tick(200,130),[])
        self.assertEqual(self.engine.public_state(now=200)['rules'][0]['status'],'warming')
        reloaded=AlertEngine(self.engine.path)
        self.assertEqual(len(reloaded.state['rules']),1)
        self.assertEqual(reloaded.public_state(now=210)['rules'][0]['status'],'warming')
    def test_pause_enable_and_delete(self):
        rule=self.rule()
        for t in [0,15,30,45]:self.tick(t,100)
        self.engine.toggle_rule(rule['id'],False,now=50)
        self.assertEqual(self.tick(60,110),[])
        self.engine.toggle_rule(rule['id'],True,now=65)
        self.assertEqual(self.tick(75,120),[])
        self.engine.remove_symbol('BTCUSDT')
        self.assertEqual(self.engine.state['rules'],[])
    def test_bad_samples_no_alert_and_key_never_public(self):
        self.rule()
        for p in [0,-1,float('nan'),float('inf'),None]:self.assertEqual(self.tick(60,p),[])
        self.engine.configure_wechat(True,'SCT1234567890testing')
        self.assertNotIn('SCT1234567890testing',json.dumps(self.engine.public_state()))
        with self.assertRaises(ValueError):self.engine.configure_wechat(True,'http://evil.example')
    def test_wechat_provider_success_and_failure(self):
        self.engine.configure_wechat(True,'SCT1234567890testing')
        async def run():
            async with httpx.AsyncClient(transport=httpx.MockTransport(lambda req:httpx.Response(200,json={'code':0}))) as client:
                self.assertEqual((await self.engine.send_wechat(client,test=True))[0],'sent')
            async with httpx.AsyncClient(transport=httpx.MockTransport(lambda req:httpx.Response(200,json={'code':400}))) as client:
                self.assertEqual((await self.engine.send_wechat(client,test=True))[0],'failed')
            async with httpx.AsyncClient(transport=httpx.MockTransport(lambda req:httpx.Response(200,json={'code':0,'data':{'errno':400}}))) as client:
                status,error=await self.engine.send_wechat(client,test=True)
                self.assertEqual(status,'failed')
                self.assertIn('通道',error)
            def timeout(request):raise httpx.ReadTimeout('secret URL must not be exposed')
            async with httpx.AsyncClient(transport=httpx.MockTransport(timeout)) as client:
                status,error=await self.engine.send_wechat(client,test=True)
                self.assertEqual(status,'failed')
                self.assertIn('结果未知',error)
                self.assertNotIn('secret',error)
        asyncio.run(run())
    def test_notification_settings_origin_and_secret_validation(self):
        async def no_poll():await asyncio.Event().wait()
        with patch.object(m,'alert_engine',self.engine),patch.object(m,'_poll_loop',no_poll),TestClient(m.app) as client:
            response=client.post('/api/alerts/wechat',json={'enabled':False},headers={'Origin':'https://unrelated.example'})
            self.assertEqual(response.status_code,403)
            secret='SCT'+'a'*220
            response=client.post('/api/alerts/wechat',json={'enabled':True,'sendKey':secret})
            self.assertEqual(response.status_code,422)
            self.assertNotIn(secret,response.text)
            self.assertEqual(client.post('/api/alerts/wechat',json={'enabled':True}).status_code,400)
            self.assertEqual(client.post('/api/alerts/wechat/test').status_code,400)

    def test_api_rule_validation_and_dispatches_actual_trigger(self):
        async def no_poll():await asyncio.Event().wait()
        now=time.time()
        requests=[]
        def handler(request):
            requests.append(request)
            return httpx.Response(200,json={'code':0})
        with patch.object(m,'alert_engine',self.engine),patch.object(m,'_poll_loop',no_poll),patch.object(m,'_get_watchlist',return_value=[{'symbol':'BTCUSDT','name':'比特币'}]),TestClient(m.app) as client:
            self.assertEqual(client.post('/api/alerts/rules',json={'symbol':'NOTWATCHED'}).status_code,400)
            self.assertEqual(client.post('/api/alerts/rules',json={'symbol':'BTCUSDT','windowMinutes':0}).status_code,422)
            self.assertEqual(client.post('/api/alerts/rules',json={'symbol':'BTCUSDT','direction':'other'}).status_code,422)
            created=client.post('/api/alerts/rules',json={'symbol':'BTCUSDT','windowMinutes':1,'thresholdPct':1})
            self.assertEqual(created.status_code,200)
            rule=self.engine.state['rules'][0];rule['startedAt']=now-120
            self.engine.samples['BTCUSDT']=deque([(now-i,100) for i in [75,60,45,30,15]])
            self.engine.configure_wechat(True,'SCT1234567890testing')
            m._last_prices.clear();m._price_times.clear()
            fake=httpx.AsyncClient(transport=httpx.MockTransport(handler))
            with patch.object(m,'_notification_client',fake),patch.object(m,'fetch_prices',AsyncMock(return_value=[{'symbol':'BTCUSDT','price':102}])):
                self.assertEqual(client.get('/api/prices').status_code,200)
                result=None
                for _ in range(20):
                    result=client.get('/api/alerts').json()
                    if result['events'] and result['events'][0]['wechatStatus']=='sent':break
                self.assertEqual(result['events'][0]['changePct'],2)
                self.assertEqual(result['events'][0]['wechatStatus'],'sent')
                self.assertEqual(len(requests),1)
                self.assertNotIn('SCT1234567890testing',json.dumps(result))
            self.assertEqual(client.patch('/api/alerts/rules/'+rule['id'],json={'enabled':False}).status_code,200)
            self.assertEqual(client.delete('/api/alerts/rules/'+rule['id']).status_code,200)

if __name__=='__main__':unittest.main()
