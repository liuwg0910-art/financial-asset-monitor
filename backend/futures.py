"""International commodity futures identifiers and Yahoo quote adapter."""
import math
import re
from datetime import datetime, timezone

import httpx
import domestic_futures as domestic

# Root, localized name, Yahoo exchange suffix, quotation unit, search aliases.
PRODUCTS = {
    "BZ": ("布伦特原油", "NYM", "美元/桶", "brent 布油"),
    "CL": ("WTI原油", "NYM", "美元/桶", "wti 美油 石油"),
    "NG": ("天然气", "NYM", "美元/百万英热单位", "natural gas"),
    "GC": ("COMEX黄金", "CMX", "美元/盎司", "gold 黄金期货"),
    "SI": ("COMEX白银", "CMX", "美元/盎司", "silver 白银期货"),
    "HG": ("COMEX铜", "CMX", "美元/磅", "copper 铜期货"),
    "ZC": ("CBOT玉米", "CBT", "美分/蒲式耳", "corn"),
    "ZW": ("CBOT小麦", "CBT", "美分/蒲式耳", "wheat"),
    "ZS": ("CBOT大豆", "CBT", "美分/蒲式耳", "soybean"),
}
MONTHS = "FGHJKMNQUVXZ"


def contract_info(symbol):
    """Only route known roots and matching exchanges; never guess stock codes."""
    symbol = symbol.strip().upper()
    local = domestic.contract_info(symbol)
    if local:
        return local
    continuous = re.fullmatch(r"([A-Z]{2})=F", symbol)
    dated = re.fullmatch(r"([A-Z]{2})([FGHJKMNQUVXZ])(\d{2})\.([A-Z]{3})", symbol)
    root = continuous[1] if continuous else dated[1] if dated else None
    if root not in PRODUCTS:
        return None
    name, exchange, unit, aliases = PRODUCTS[root]
    if dated and dated[4] != exchange:
        return None
    month = f"20{dated[3]}-{MONTHS.index(dated[2])+1:02d}" if dated else None
    return dict(symbol=symbol, name=f"{name} {month or '连续合约'}", market="futures",
                instrumentType="futures", contractType="dated" if dated else "continuous",
                contractMonth=month, exchange=exchange, unit=unit, aliases=aliases)


def search_contracts(query, now=None):
    """Show continuous benchmarks and dated candidates; add verifies availability."""
    now = now or datetime.now(timezone.utc)
    candidates = domestic.search_contracts(query, now) + [contract_info(f"{root}=F") for root in PRODUCTS]
    exact = contract_info(query)
    if exact:
        candidates.insert(0, exact)
    for root, (_, exchange, _, _) in PRODUCTS.items():
        for offset in range(24):
            year, month = divmod(now.year * 12 + now.month - 1 + offset, 12)
            candidates.append(contract_info(f"{root}{MONTHS[month]}{year % 100:02d}.{exchange}"))
    q = query.casefold()
    return [i for i in candidates if q in f"{i['symbol']} {i['name']} {i['aliases']} 期货 futures".casefold()]


def parse_futures_quote(info, meta):
    price = float(meta["regularMarketPrice"])
    timestamp = int(meta["regularMarketTime"])
    if not math.isfinite(price) or price <= 0 or timestamp <= 0:
        raise ValueError("Invalid futures price or timestamp")
    def number(value):
        try:
            value = float(value)
            return value if math.isfinite(value) else None
        except (ValueError, TypeError):
            return None
    previous = number(meta.get("previousClose", meta.get("chartPreviousClose")))
    change = price - previous if previous is not None and previous > 0 else None
    quoted = datetime.fromtimestamp(timestamp, timezone.utc).isoformat()
    return {**{k: v for k, v in info.items() if k != "aliases"}, "price": price,
            "change": change, "changePct": change / previous * 100 if change is not None else None,
            "high": number(meta.get("regularMarketDayHigh")),
            "low": number(meta.get("regularMarketDayLow")),
            "open": number(meta.get("regularMarketOpen")), "close": previous,
            "volume": number(meta.get("regularMarketVolume")),
            "providerContract": meta.get("shortName"),
            "source": "yahoo", "quoteTime": quoted, "updated": quoted,
            "receivedAt": datetime.now(timezone.utc).isoformat(),
            "notice": "公开期货报价可能延迟；连续合约换月可能跳空，预警按本站采样计算。"}


async def fetch_futures_quote(client, info):
    if info.get('region') == '中国内地':
        return await domestic.fetch_quote(client, info)
    for host in ("query1.finance.yahoo.com", "query2.finance.yahoo.com"):
        try:
            response = await client.get(f"https://{host}/v8/finance/chart/{info['symbol']}",
                                        params={"interval": "1d", "range": "1d"}, timeout=10)
            response.raise_for_status()
            payload = response.json()["chart"]
            if payload.get("error"):
                raise ValueError("Upstream error")
            meta = payload["result"][0]["meta"]
            # Prevent an upstream fallback from silently monitoring another contract.
            if meta.get("symbol", "").upper() != info["symbol"]:
                raise ValueError("Contract mismatch")
            return parse_futures_quote(info, meta)
        except (httpx.HTTPError, ValueError, KeyError, TypeError, IndexError, OverflowError):
            continue
    return None
