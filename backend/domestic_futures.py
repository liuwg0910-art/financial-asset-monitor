"""Sina domestic futures quotes and real historical bars (no synthetic data)."""
import json
import asyncio
import logging
import math
import re
from datetime import datetime, timedelta, timezone

import httpx


async def _get(client, url, **kwargs):
    for attempt in range(2):
        try:
            response=await client.get(url,**kwargs)
            response.raise_for_status()
            return response
        except httpx.HTTPError:
            if attempt:raise
            await asyncio.sleep(0.2)

CN_TZ = timezone(timedelta(hours=8))
PRODUCTS = {
    'RB': ('螺纹钢','上期所','元/吨'), 'HC': ('热轧卷板','上期所','元/吨'),
    'AU': ('沪金','上期所','元/克'), 'AG': ('沪银','上期所','元/千克'),
    'CU': ('沪铜','上期所','元/吨'), 'AL': ('沪铝','上期所','元/吨'),
    'ZN': ('沪锌','上期所','元/吨'), 'NI': ('沪镍','上期所','元/吨'),
    'SN': ('沪锡','上期所','元/吨'), 'RU': ('橡胶','上期所','元/吨'),
    'BU': ('沥青','上期所','元/吨'), 'FU': ('燃料油','上期所','元/吨'),
    'SC': ('上海原油','能源中心','元/桶'), 'LU': ('低硫燃料油','能源中心','元/吨'),
    'NR': ('20号胶','能源中心','元/吨'), 'EC': ('集运欧线','能源中心','点'),
    'I': ('铁矿石','大商所','元/吨'), 'J': ('焦炭','大商所','元/吨'),
    'JM': ('焦煤','大商所','元/吨'), 'M': ('豆粕','大商所','元/吨'),
    'Y': ('豆油','大商所','元/吨'), 'P': ('棕榈油','大商所','元/吨'),
    'C': ('玉米','大商所','元/吨'), 'A': ('豆一','大商所','元/吨'),
    'EG': ('乙二醇','大商所','元/吨'), 'PP': ('聚丙烯','大商所','元/吨'),
    'TA': ('PTA','郑商所','元/吨'), 'MA': ('甲醇','郑商所','元/吨'),
    'SA': ('纯碱','郑商所','元/吨'), 'FG': ('玻璃','郑商所','元/吨'),
    'SR': ('白糖','郑商所','元/吨'), 'CF': ('棉花','郑商所','元/吨'),
    'AP': ('苹果','郑商所','元/吨'), 'UR': ('尿素','郑商所','元/吨'),
    'LC': ('碳酸锂','广期所','元/吨'), 'SI': ('工业硅','广期所','元/吨'),
    'PS': ('多晶硅','广期所','元/吨'),
    'IF': ('沪深300股指','中金所','点'), 'IH': ('上证50股指','中金所','点'),
    'IC': ('中证500股指','中金所','点'), 'IM': ('中证1000股指','中金所','点'),
    'T': ('十年期国债','中金所','元/百元面值'),
    'TF': ('五年期国债','中金所','元/百元面值'),
    'TS': ('二年期国债','中金所','元/百元面值'),
    'TL': ('三十年期国债','中金所','元/百元面值'),
}


def contract_info(symbol):
    match = re.fullmatch(r'([A-Z]{1,2})(0|\d{4})', symbol.strip().upper())
    if not match or match[1] not in PRODUCTS:
        return None
    root, suffix = match.groups()
    if suffix != '0' and not 1 <= int(suffix[-2:]) <= 12:
        return None
    name, exchange, unit = PRODUCTS[root]
    month = f'20{suffix[:2]}-{suffix[2:]}' if suffix != '0' else None
    return dict(symbol=root+suffix, name=f'{name} {month or "主力连续"}', market='futures',
                instrumentType='futures', contractType='dated' if month else 'continuous',
                contractMonth=month, exchange=exchange, unit=unit, region='中国内地',
                aliases=f'国内期货 {root} {name} {exchange} '+
                        (root+suffix[1:]+' ' if month and exchange=='郑商所' else '')+
                        ('黄金' if root=='AU' else '白银' if root=='AG' else ''))


def search_contracts(query, now=None):
    now = now or datetime.now(CN_TZ)
    rows = [contract_info(root+'0') for root in PRODUCTS]
    exact = contract_info(query)
    if exact:
        rows.insert(0, exact)
    for root in PRODUCTS:
        for offset in range(18):
            year, month = divmod(now.year*12+now.month-1+offset,12)
            rows.append(contract_info(f'{root}{year%100:02d}{month+1:02d}'))
    q=query.casefold()
    return [r for r in rows if q in f'{r["symbol"]} {r["name"]} {r["aliases"]}'.casefold()]


def parse_quote(info, text):
    match=re.search(r'hq_str_nf_'+re.escape(info['symbol'])+r'="([^"]*)"',text)
    if not match or not match[1]:
        raise ValueError('No domestic futures quote')
    p=match[1].split(',')
    def num(index):
        value=float(p[index])
        if not math.isfinite(value):raise ValueError('Nonfinite quote')
        return value
    if info['exchange']=='中金所':
        price, opening, high, low, previous=num(3),num(0),num(1),num(2),num(13)
        volume, position=num(4),num(6)
        stamp=p[36]+' '+p[37]
        provider=p[-1]
    else:
        price, opening, high, low, previous=num(8),num(2),num(3),num(4),num(10)
        volume, position=num(14),num(13)
        clock=p[1].zfill(6)
        stamp=p[17]+' '+':'.join([clock[:2],clock[2:4],clock[4:6]])
        provider=p[0]
    if price<=0 or previous<=0:raise ValueError('Invalid domestic quote')
    quoted=datetime.strptime(stamp,'%Y-%m-%d %H:%M:%S').replace(tzinfo=CN_TZ).astimezone(timezone.utc).isoformat()
    return {**{k:v for k,v in info.items() if k!='aliases'}, 'price':price,
            'open':opening or None,'high':high or None,'low':low or None,'close':previous,
            'previousSettlement':previous,'change':price-previous,'changePct':(price/previous-1)*100,
            'volume':volume,'openInterest':position,'providerContract':provider,'source':'sina',
            'quoteTime':quoted,'updated':quoted,'receivedAt':datetime.now(timezone.utc).isoformat()}


async def fetch_quote(client, info):
    try:
        r=await _get(client,'https://hq.sinajs.cn/list=nf_'+info['symbol'],
                           headers={'Referer':'https://finance.sina.com.cn/'},timeout=10)
        r.raise_for_status()
        return parse_quote(info,r.content.decode('gbk',errors='replace'))
    except (httpx.HTTPError,ValueError,IndexError,TypeError,OverflowError) as exc:
        logging.getLogger(__name__).warning('Domestic quote unavailable: %s (%s)',info['symbol'],type(exc).__name__)
        return None


async def fetch_kline(client, symbol, interval):
    method='getDailyKLine' if interval=='1d' else 'getFewMinLine'
    params={'symbol':symbol}
    if interval!='1d':params['type']={'1m':'1','5m':'5','15m':'15','1h':'60'}[interval]
    r=await _get(client,'https://stock2.finance.sina.com.cn/futures/api/jsonp.php/var%20_data=/InnerFuturesNewService.'+method,
                       params=params,headers={'Referer':'https://finance.sina.com.cn/'},timeout=15)
    r.raise_for_status()
    match=re.search(r'var\s+_data\s*=\s*\((.*)\)\s*;?\s*$',r.text,re.S)
    if not match:raise ValueError('Invalid futures K-line response')
    rows=json.loads(match[1])
    if rows is None:return []
    if not isinstance(rows,list):raise ValueError('Invalid futures bars')
    return [dict(time=int(datetime.fromisoformat(row['d']).replace(tzinfo=CN_TZ).timestamp()),
                 open=row['o'],high=row['h'],low=row['l'],close=row['c'],volume=row.get('v',0)) for row in rows]
