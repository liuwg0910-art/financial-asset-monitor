"""Rolling quote alerts, durable rules/history, and opt-in WeChat delivery."""
import asyncio
import json
import math
import logging
import os
import re
import time
import uuid
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
import httpx


class _RedactPushCredentials(logging.Filter):
    def filter(self,record):
        message=record.getMessage()
        if 'sctapi.ftqq.com/' in message:
            record.msg=re.sub(r'(sctapi\.ftqq\.com/)[^\s"?]+',r'\1[REDACTED]',message)
            record.args=()
        return True

logging.getLogger('httpx').addFilter(_RedactPushCredentials())


def iso(ts):return datetime.fromtimestamp(ts,timezone.utc).isoformat()

class AlertEngine:
    def __init__(self,path):
        self.path=Path(path)
        self.samples={}
        self.active={}
        self.futures_ticks={}
        self.basis_active={}
        self.state={'rules':[],'basisRules':[],'events':[],'wechat':{'enabled':False,'sendKey':''}}
        try:
            stored=json.loads(self.path.read_text(encoding='utf-8'))
            if not isinstance(stored,dict) or not isinstance(stored.get('rules'),list) or not isinstance(stored.get('events'),list):
                raise ValueError('Invalid alert state')
            self.state.update(stored)
        except FileNotFoundError:pass
        # A crash may leave an in-flight delivery uncertain; avoid duplicate WeChat sends.
        pending=[e for e in self.state['events'] if e.get('wechatStatus')=='pending']
        for event in pending:
            event.update(wechatStatus='failed',wechatError='服务重启时推送结果未知，请检查微信是否收到')
        if pending:self.save()
        # Corrupt state is surfaced instead of silently losing rules or credentials.

    def save(self):
        temp=self.path.with_suffix('.tmp')
        temp.write_text(json.dumps(self.state,ensure_ascii=False,indent=2),encoding='utf-8')
        os.replace(temp,self.path)

    def add_rule(self,data,now=None):
        now=time.time() if now is None else now
        if len(self.state['rules'])>=50:raise ValueError('最多设置50条预警')
        rule={**data,'id':uuid.uuid4().hex,'createdAt':iso(now),'startedAt':now,'lastTriggeredAt':None}
        self.state['rules'].append(rule)
        self.save()
        return rule

    def toggle_rule(self,rid,enabled,now=None):
        rule=next((r for r in self.state['rules'] if r['id']==rid),None)
        if rule is None:raise KeyError(rid)
        if enabled and not rule['enabled']:
            rule['startedAt']=time.time() if now is None else now
        rule['enabled']=enabled
        self.active.pop(rid,None)
        self.save()
        return rule

    def delete_rule(self,rid):
        before=len(self.state['rules'])
        self.state['rules']=[r for r in self.state['rules'] if r['id']!=rid]
        if len(self.state['rules'])==before:raise KeyError(rid)
        self.active.pop(rid,None)
        self.save()

    def remove_symbol(self,symbol):
        previous=len(self.state['rules'])
        self.state['rules']=[r for r in self.state['rules'] if r['symbol']!=symbol]
        self.samples.pop(symbol,None)
        if len(self.state['rules'])!=previous:self.save()

    def observe(self,quotes,now=None):
        now=time.time() if now is None else now
        watched={r['symbol'] for r in self.state['rules'] if r['enabled']}
        for symbol in list(self.samples):
            if symbol not in watched:self.samples.pop(symbol,None)
        for symbol in list(self.futures_ticks):
            if symbol not in watched:self.futures_ticks.pop(symbol,None)
        emitted=[]
        for quote in quotes:
            symbol=quote.get('symbol','').upper()
            if symbol not in watched:continue
            try:price=float(quote['price'])
            except (KeyError,TypeError,ValueError):continue
            if not math.isfinite(price) or price<=0:continue
            if quote.get('market')=='futures':
                tick=(quote.get('quoteTime'),quote.get('providerContract'))
                previous_tick=self.futures_ticks.get(symbol)
                if not tick[0] or tick==previous_tick:continue
                if previous_tick and tick[1]!=previous_tick[1]:
                    self.samples.pop(symbol,None)
                    for r in self.state['rules']:
                        if r['symbol']==symbol:self.active.pop(r['id'],None)
                self.futures_ticks[symbol]=tick
            history=self.samples.setdefault(symbol,deque())
            if history and now<=history[-1][0]:continue
            # No comparison across service/network outages. Restart the sampling window.
            if history and now-history[-1][0]>60:
                history.clear()
                for r in self.state['rules']:
                    if r['symbol']==symbol:self.active.pop(r['id'],None)
            history.append((now,price))
            while history and history[0][0]<now-86460:history.popleft()
            for rule in self.state['rules']:
                if not rule['enabled'] or rule['symbol']!=symbol:continue
                cutoff=now-rule['windowMinutes']*60
                baseline=next(((ts,p) for ts,p in reversed(history) if ts<=cutoff and ts>=rule['startedAt']),None)
                if baseline is None or cutoff-baseline[0]>45:continue
                change=round((price/baseline[1]-1)*100,10)
                direction='up' if change>=rule['thresholdPct'] else 'down' if change<=-rule['thresholdPct'] else None
                if rule['direction']!='both' and direction!=rule['direction']:direction=None
                previous=self.active.get(rule['id'])
                self.active[rule['id']]=direction
                if direction is None or direction==previous:continue
                last=rule['lastTriggeredAt']
                if last is not None and now-last<rule['cooldownMinutes']*60:continue
                rule['lastTriggeredAt']=now
                configured=self.state['wechat']
                event={'id':uuid.uuid4().hex,'ruleId':rule['id'],'symbol':symbol,'name':rule['name'],
                       'windowMinutes':rule['windowMinutes'],'thresholdPct':rule['thresholdPct'],
                       'direction':direction,'changePct':round(change,4),'price':price,
                       'baselinePrice':baseline[1],'baselineTime':iso(baseline[0]),'triggeredAt':iso(now),
                       'actualSeconds':round(now-baseline[0]),'wechatStatus':'pending' if configured['enabled'] else 'disabled',
                       'wechatError':None}
                self.state['events'].insert(0,event)
                emitted.append(event.copy())
        if emitted:
            self.state['events']=self.state['events'][:200]
            self.save()
        return emitted

    def add_basis_rule(self,data):
        if len(self.state['basisRules'])>=20:raise ValueError('最多设置20条贴水预警')
        rule={**data,'id':uuid.uuid4().hex,'enabled':True,'lastTriggeredAt':None}
        self.state['basisRules'].append(rule)
        self.save()
        return rule

    def change_basis_rule(self,rid,enabled=None,delete=False):
        rule=next((r for r in self.state['basisRules'] if r['id']==rid),None)
        if rule is None:raise KeyError(rid)
        if delete:self.state['basisRules'].remove(rule)
        else:rule['enabled']=enabled
        self.basis_active.pop(rid,None)
        self.save()

    def observe_basis(self,items,now=None):
        now=time.time() if now is None else now
        emitted=[]
        for item in items:
            if not item.get('available') or item.get('stale'):continue
            try:rate=float(item['rate'])
            except (KeyError,TypeError,ValueError):continue
            if not math.isfinite(rate):continue
            pair=(item['frontSymbol'],item['farSymbol'])
            for rule in self.state['basisRules']:
                if not rule['enabled'] or rule['symbol']!=item['symbol']:continue
                hit=rate<=rule['thresholdPct'] if rule['operator']=='lte' else rate>=rule['thresholdPct']
                previous=self.basis_active.get(rule['id'])
                self.basis_active[rule['id']]=(pair,hit)
                if not hit or previous==(pair,True):continue
                last=rule['lastTriggeredAt']
                if last is not None and now-last<rule['cooldownMinutes']*60:continue
                rule['lastTriggeredAt']=now
                event=dict(id=uuid.uuid4().hex,kind='basis',ruleId=rule['id'],symbol=item['symbol'],
                           name=item['symbol']+'年化贴水',rate=rate,thresholdPct=rule['thresholdPct'],
                           operator=rule['operator'],frontSymbol=pair[0],farSymbol=pair[1],
                           frontPrice=item['frontPrice'],farPrice=item['farPrice'],quoteTime=item['quoteTime'],
                           triggeredAt=iso(now),wechatStatus='pending' if self.state['wechat']['enabled'] else 'disabled',wechatError=None)
                self.state['events'].insert(0,event)
                emitted.append(event.copy())
        if emitted:
            self.state['events']=self.state['events'][:200]
            self.save()
        return emitted

    def public_state(self,now=None):
        now=time.time() if now is None else now
        rules=[]
        for rule in self.state['rules']:
            history=self.samples.get(rule['symbol'],[])
            valid=[s for s in history if s[0]>=rule['startedAt']]
            elapsed=max(0,valid[-1][0]-valid[0][0]) if valid else 0
            seconds=rule['windowMinutes']*60
            status='paused' if not rule['enabled'] else 'warming'
            if rule['enabled'] and valid and now-valid[-1][0]>60:status='interrupted'
            elif rule['enabled'] and elapsed>=seconds:status='monitoring'
            rules.append({**rule,'status':status,'remainingSeconds':max(0,math.ceil(seconds-elapsed))})
        conf=self.state['wechat']
        return {'rules':rules,'basisRules':[r.copy() for r in self.state['basisRules']], 'events':[e.copy() for e in self.state['events'][:50]],
                'wechat':{'enabled':conf['enabled'],'configured':bool(conf['sendKey']),
                          'lastTest':conf.get('lastTest')},
                'sampleSeconds':15}

    def configure_wechat(self,enabled,send_key=None):
        conf=self.state['wechat']
        if send_key:
            if not re.fullmatch(r'SCT[A-Za-z0-9]{10,200}',send_key):
                raise ValueError('请填写Server酱Turbo版SCT开头的SendKey')
        key=send_key or conf['sendKey']
        if enabled and not key:raise ValueError('请先填写SendKey再启用微信通知')
        self.state['wechat']={'enabled':enabled,'sendKey':key,
                              'lastTest':conf.get('lastTest') if key==conf['sendKey'] and enabled==conf['enabled'] else None}
        self.save()

    def delivery_result(self,event_id,status,error=None):
        event=next((e for e in self.state['events'] if e['id']==event_id),None)
        if event:
            event['wechatStatus']=status
            event['wechatError']=error
            self.save()

    async def send_wechat(self,client,event=None,test=False):
        conf=self.state['wechat']
        if not conf['enabled'] or not conf['sendKey']:return ('disabled','微信通知尚未配置')
        if test:
            title='金融资产监控看板 微信通知测试'
            body='测试成功：微信预警通道已连通。达到已配置的涨跌幅条件时，将向此微信发送通知。'
        elif event.get('kind')=='basis':
            title=f"贴水预警：{event['symbol']} {event['rate']:.2f}%"
            body=(f"{event['name']}：{event['rate']:.2f}%。\n\n"
                  f"条件：{'≤' if event['operator']=='lte' else '≥'} {event['thresholdPct']}%。\n\n"
                  f"当季：{event['frontSymbol']} {event['frontPrice']}；下季：{event['farSymbol']} {event['farPrice']}。\n\n"
                  f"计算：（下季－当季）/当季×4×100%。报价时间（UTC）：{event['quoteTime']}。")
        else:
            verb='上涨' if event['direction']=='up' else '下跌'
            title=f"行情预警：{event['symbol']} {verb}{abs(event['changePct']):.2f}%"
            body=(f"{event['name']} ({event['symbol']})\n\n"
                  f"在约{event['windowMinutes']}分钟内{verb}{abs(event['changePct']):.2f}%，阈值{event['thresholdPct']}%。\n\n"
                  f"起始价：{event['baselinePrice']}；当前价：{event['price']}。\n\n"
                  f"触发时间（UTC）：{event['triggeredAt']}。实际采样跨度{event['actualSeconds']}秒。")
        try:
            response=await client.post(f"https://sctapi.ftqq.com/{conf['sendKey']}.send",
                                       data={'title':title[:32],'desp':body,'noip':'1'},timeout=10)
            response.raise_for_status()
            result=response.json()
            if not isinstance(result,dict):return ('failed','微信服务返回格式异常，请稍后重试')
            if result.get('code')!=0:
                code=result.get('code')
                safe_code=str(code) if isinstance(code,int) else '未知'
                return ('failed',f'微信服务拒绝请求（错误码 {safe_code}）。请在Server酱官网检查SendKey、消息通道和剩余额度')
            data=result.get('data')
            if isinstance(data,dict) and data.get('errno') not in (None,0):
                return ('failed','微信消息通道返回失败，请在Server酱官网检查通道绑定及发送记录')
            return ('sent',None)
        except httpx.PoolTimeout:
            return ('failed','通知请求等待连接超时，行情请求繁忙，请稍后再试')
        except httpx.TimeoutException:
            return ('failed','连接微信推送服务超时，发送结果未知；请先检查微信及Server酱发送记录，避免重复发送')
        except httpx.HTTPStatusError as exc:
            return ('failed',f'微信推送服务返回HTTP {exc.response.status_code}，请检查服务状态与额度')
        except httpx.ConnectError:
            return ('failed','服务器连接Server酱失败（DNS、代理或TLS连接异常）。官网测试正常不代表本程序运行环境能够连接')
        except (httpx.HTTPError,ValueError):
            # Never log request URLs: they contain the SendKey.
            return ('failed','微信推送请求失败，请检查网络或稍后重试')
