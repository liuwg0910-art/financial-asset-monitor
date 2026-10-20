"""IC/IM quarterly spread, using matched real quotes and daily closing prices."""
import asyncio
import calendar
import math
import time
import json
import logging
import os
from pathlib import Path
from datetime import date, datetime, timedelta

import domestic_futures as domestic

ROOTS = ('IC', 'IM')


def expiry_day(year, month):
    first = date(year, month, 1)
    return first + timedelta(days=(4-first.weekday()) % 7 + 14)


def quarter_pair(root, day, expiries=None):
    """Nearest unexpired quarterly contract; expiry day remains the front leg."""
    expiries = expiries or {}
    year, month = day.year, ((day.month-1)//3+1)*3
    front = f'{root}{year%100:02d}{month:02d}'
    if day > expiries.get(front, expiry_day(year, month)):
        year, month = (year+1, 3) if month == 12 else (year, month+3)
    far_year, far_month = (year+1, 3) if month == 12 else (year, month+3)
    return f'{root}{year%100:02d}{month:02d}', f'{root}{far_year%100:02d}{far_month:02d}'


def annualized(front, far):
    front, far = float(front), float(far)
    if not all(math.isfinite(v) and v > 0 for v in (front, far)):
        raise ValueError('Invalid leg prices')
    return (far-front) / front * 4 * 100


class BasisService:
    def __init__(self, cache_path=None):
        self.current_cache = None
        self.history_cache = None
        self.current_lock = asyncio.Lock()
        self.history_lock = asyncio.Lock()
        self.expiries = {}
        self.cache_path = Path(cache_path) if cache_path else None
        self.saved = {'current':{},'history':None}
        if self.cache_path:
            try:
                value=json.loads(self.cache_path.read_text(encoding='utf-8'))
                if isinstance(value,dict) and isinstance(value.get('current'),dict):self.saved.update(value)
            except (OSError,ValueError):pass

    def save_snapshot(self):
        if not self.cache_path:return
        try:
            temp=self.cache_path.with_suffix('.tmp')
            temp.write_text(json.dumps(self.saved,ensure_ascii=False),encoding='utf-8')
            os.replace(temp,self.cache_path)
        except OSError:
            logging.getLogger(__name__).warning('Unable to persist basis snapshot')

    async def current(self, client):
        async with self.current_lock:
            now = datetime.now(domestic.CN_TZ)
            pairs = {root: quarter_pair(root, now.date(), self.expiries) for root in ROOTS}
            # Switch after the expiry session, never during that day's trading.
            for root, (front, _) in pairs.copy().items():
                last_day = self.expiries.get(front, expiry_day(2000+int(front[2:4]), int(front[4:])))
                if now.date() == last_day and now.hour >= 15:
                    pairs[root] = quarter_pair(root, now.date()+timedelta(days=1), self.expiries)
            key = tuple(pairs.items())
            ttl=30 if self.current_cache and all(i.get('available') and not i.get('cached') for i in self.current_cache[2]['items']) else 5
            if self.current_cache and self.current_cache[0] == key and time.monotonic()-self.current_cache[1] < ttl:
                return self.current_cache[2]
            symbols = sorted({s for pair in pairs.values() for s in pair})
            async def quote(s):
                return s, await domestic.fetch_quote(client, domestic.contract_info(s))
            quotes = dict(await asyncio.gather(*(quote(s) for s in symbols)))
            items = []
            for root, (front, far) in pairs.items():
                item = dict(symbol=root, frontSymbol=front, farSymbol=far, rate=None, available=False)
                a, b = quotes[front], quotes[far]
                if a and b:
                    ta, tb = (datetime.fromisoformat(q['quoteTime']) for q in (a,b))
                    age = max((now-ta).total_seconds(), (now-tb).total_seconds())
                    skew = abs((ta-tb).total_seconds())
                    if ta.astimezone(domestic.CN_TZ).date() == tb.astimezone(domestic.CN_TZ).date() and skew <= 60:
                        item.update(rate=annualized(a['price'], b['price']), frontPrice=a['price'], farPrice=b['price'],
                                    frontQuoteTime=a['quoteTime'], farQuoteTime=b['quoteTime'],
                                    quoteTime=min(ta,tb).isoformat(), skewSeconds=skew, available=True,
                                    stale=age > 120 or age < -60)
                    else:
                        item['error'] = '两腿报价日期不一致或相差超过60秒，暂停计算'
                else:
                    item['error'] = '季月合约报价暂不可用'
                if item['available']:
                    self.saved['current'][root]=item.copy()
                else:
                    saved=self.saved['current'].get(root)
                    if saved and (saved.get('frontSymbol'),saved.get('farSymbol'))==(front,far):
                        item={**saved,'stale':True,'cached':True,'error':item['error']}
                items.append(item)
            result = dict(items=items, refreshSeconds=30, source='新浪财经',
                          formula='（下季价格－当季价格）÷当季价格×4×100%',
                          notice='数值越负，贴水越深；这是跨季年化价差，不是相对现货的基差或保证金收益率。')
            self.current_cache = (key,time.monotonic(),result)
            if any(i.get('available') and not i.get('cached') for i in items):self.save_snapshot()
            return result

    async def history(self, client):
        async with self.history_lock:
            today = datetime.now(domestic.CN_TZ).date()
            ttl=3600 if self.history_cache and not self.history_cache[2].get('failedContracts') and not self.history_cache[2].get('cached') else 15
            if self.history_cache and self.history_cache[0] == today and time.monotonic()-self.history_cache[1] < ttl:
                return self.history_cache[2]
            start = today-timedelta(days=365)
            symbols = set()
            # Fetch one extra surrounding quarter to resolve expiry holidays from actual last bars.
            for root in ROOTS:
                for year in range(start.year, today.year+2):
                    for month in (3,6,9,12):
                        if start-timedelta(days=100) <= expiry_day(year,month) <= today+timedelta(days=190):
                            symbols.add(f'{root}{year%100:02d}{month:02d}')
            semaphore = asyncio.Semaphore(3)
            failed = []
            async def bars(symbol):
                async with semaphore:
                    try:
                        data = await domestic.fetch_kline(client,symbol,'1d')
                        clean = {}
                        for row in data:
                            day = datetime.fromtimestamp(row['time'],domestic.CN_TZ).date()
                            close = float(row['close'])
                            if math.isfinite(close) and close > 0:
                                clean[day] = close
                        if not clean:failed.append(symbol)
                        return symbol, clean
                    except Exception:
                        failed.append(symbol)
                        return symbol, {}
            raw = dict(await asyncio.gather(*(bars(s) for s in sorted(symbols))))
            for symbol, values in raw.items():
                year,month = 2000+int(symbol[2:4]),int(symbol[4:])
                if values and expiry_day(year,month)+timedelta(days=10) < today:
                    last = max(values)
                    # Do not treat a truncated provider series as the true expiry.
                    if abs((last-expiry_day(year,month)).days) <= 10:
                        self.expiries[symbol] = last
            series = []
            for root in ROOTS:
                days = sorted({day for s,values in raw.items() if s.startswith(root) for day in values if start <= day < today})
                points, missing = [], 0
                for day in days:
                    front,far = quarter_pair(root,day,self.expiries)
                    a,b = raw.get(front,{}).get(day),raw.get(far,{}).get(day)
                    if a is None or b is None:
                        missing += 1
                        points.append(dict(date=day.isoformat(),rate=None,frontSymbol=front,farSymbol=far))
                    else:
                        points.append(dict(date=day.isoformat(),rate=annualized(a,b),frontSymbol=front,farSymbol=far,
                                           frontPrice=a,farPrice=b))
                series.append(dict(symbol=root,points=points,missingDays=missing))
            result = dict(series=series,startDate=start.isoformat(),endDate=(today-timedelta(days=1)).isoformat(),
                          failedContracts=failed,source='新浪财经真实日线收盘价',
                          notice='历史按当日对应季月合约同日收盘价重建，不补点；当日实时值单列。换季处断线，缺失数据不插值。遇节假日顺延按已到期合约末条日线识别，未来到期暂按第三个周五估算。')
            saved=self.saved.get('history')
            usable=any(p['rate'] is not None for s in series for p in s['points'])
            if saved:
                # Keep each previously valid series when a transient outage gives fewer points.
                for series in result['series']:
                    old=next((s for s in saved.get('series',[]) if s['symbol']==series['symbol']),None)
                    if old and sum(p['rate'] is not None for p in old['points']) > sum(p['rate'] is not None for p in series['points']):
                        series.update(old)
                        result.update(cached=True, snapshotEndDate=saved['endDate'])
            if usable and not result.get('cached'):
                self.saved['history']=result
                self.save_snapshot()
            # An outage must never hide recovered data for an entire hour.
            result['retrySeconds']=15 if failed or result.get('cached') or not usable else 3600
            self.history_cache=(today,time.monotonic(),result)
            return result
