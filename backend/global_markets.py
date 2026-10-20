"""Independent global benchmark board; never reads or changes the watchlist."""
import asyncio
import math
import time
from datetime import datetime, timezone
import httpx

# symbol, name, category, region/sector, display unit
_BENCHMARKS = [
    ('000001.SS','上证指数','index','中国内地','点'),
    ('399001.SZ','深证成指','index','中国内地','点'),
    ('000300.SS','沪深300','index','中国内地','点'),
    ('^HSI','恒生指数','index','中国香港','点'),
    ('^N225','日经225','index','日本','点'),
    ('^KS11','韩国综合指数','index','韩国','点'),
    ('^GSPC','标普500','index','美国','点'),
    ('^DJI','道琼斯工业指数','index','美国','点'),
    ('^IXIC','纳斯达克综合指数','index','美国','点'),
    ('^FTSE','英国富时100','index','英国','点'),
    ('^GDAXI','德国DAX','index','德国','点'),
    ('^FCHI','法国CAC40','index','法国','点'),
    ('^SPGSCI','标普GSCI商品指数','commodity','综合商品','点'),
    ('GC=F','COMEX黄金','commodity','贵金属','美元/盎司'),
    ('SI=F','COMEX白银','commodity','贵金属','美元/盎司'),
    ('HG=F','COMEX铜','commodity','工业金属','美元/磅'),
    ('CL=F','WTI原油','commodity','能源','美元/桶'),
    ('BZ=F','布伦特原油','commodity','能源','美元/桶'),
    ('NG=F','NYMEX天然气','commodity','能源','美元/百万英热单位'),
    ('ZC=F','CBOT玉米','commodity','农产品','美分/蒲式耳'),
    ('ZW=F','CBOT小麦','commodity','农产品','美分/蒲式耳'),
    ('ZS=F','CBOT大豆','commodity','农产品','美分/蒲式耳'),
]
BENCHMARKS = [dict(symbol=s,name=n,category=c,region=r,unit=u,instrumentType="index" if c == "index" or s == "^SPGSCI" else "futures") for s,n,c,r,u in _BENCHMARKS]
_cache = {}
_last_attempt = None
_lock = asyncio.Lock()


def parse_quote(meta):
    price = float(meta['regularMarketPrice'])
    timestamp = int(meta['regularMarketTime'])
    if not math.isfinite(price) or price <= 0 or timestamp <= 0:
        raise ValueError('Invalid benchmark quote')
    # range=1d: chartPreviousClose is the previous trading close, not a multi-day baseline.
    previous = meta.get('previousClose')
    if previous is None:
        previous = meta.get('chartPreviousClose')
    previous = float(previous) if previous is not None else None
    if previous is not None and (not math.isfinite(previous) or previous <= 0):
        previous = None
    return dict(price=price,previousClose=previous,
                change=price-previous if previous is not None else None,
                changePct=(price/previous-1)*100 if previous is not None else None,
                quoteTime=datetime.fromtimestamp(timestamp,timezone.utc).isoformat(),
                source='yahoo',stale=False,unavailable=False)


async def _fetch_quote(client, benchmark):
    for host in ('query1.finance.yahoo.com','query2.finance.yahoo.com'):
        try:
            response=await client.get(f'https://{host}/v8/finance/chart/{benchmark["symbol"]}',
                                      params={'interval':'1d','range':'1d'},timeout=10)
            response.raise_for_status()
            chart=response.json()['chart']
            if chart.get('error'):
                raise ValueError('Upstream chart error')
            return {**benchmark,**parse_quote(chart['result'][0]['meta'])}
        except (httpx.HTTPError,ValueError,KeyError,TypeError,IndexError,OverflowError):
            if host.startswith('query2'):
                return None


async def get_global_markets(client):
    global _last_attempt
    async with _lock:
        if _last_attempt is None or time.monotonic()-_last_attempt >= 60:
            semaphore=asyncio.Semaphore(5)
            async def fetch_one(benchmark):
                async with semaphore:
                    quote=await _fetch_quote(client,benchmark)
                    key=benchmark['symbol']
                    if quote:
                        _cache[key]=quote
                    elif key in _cache and not _cache[key].get('unavailable'):
                        _cache[key]={**_cache[key],'stale':True}
                    else:
                        _cache[key]={**benchmark,'price':None,'previousClose':None,'change':None,
                                     'changePct':None,'quoteTime':None,'source':'yahoo',
                                     'stale':False,'unavailable':True}
            await asyncio.gather(*(fetch_one(b) for b in BENCHMARKS))
            _last_attempt=time.monotonic()
        return {'items':[_cache[b['symbol']] for b in BENCHMARKS],'refreshSeconds':60,
                'source':'Yahoo Finance','notice':'股指以点数显示；商品包含综合指数及期货基准报价，可能存在延迟。报价时间为数据源最近成交时间，休市时不更新。'}
