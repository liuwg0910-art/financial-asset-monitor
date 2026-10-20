"""
金融资产监控看板 — 后端服务
FastAPI + WebSocket + 多数据源聚合
运行: python main.py
"""

import asyncio
import json
import re
import logging
import math
import os
from pathlib import Path
from contextlib import asynccontextmanager
import time
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import quote, urlparse

import httpx
from fastapi import FastAPI, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from global_markets import get_global_markets
from futures import contract_info, search_contracts, fetch_futures_quote
import domestic_futures
from alerts import AlertEngine
from basis import BasisService
from pydantic import BaseModel, Field
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from fastapi.exception_handlers import request_validation_exception_handler

# ── App ────────────────────────────────────────────────────
logger = logging.getLogger(__name__)
_client = None
_notification_client = None
_basis_client = None
_basis_history_client = None
_refresh_lock = asyncio.Lock()
_watchlist_lock = asyncio.Lock()

@asynccontextmanager
async def lifespan(app):
    global _client, _basis_client, _basis_history_client, _notification_client, _alert_queue
    _alert_queue = asyncio.Queue(maxsize=200)
    alert_engine.samples.clear()
    alert_engine.active.clear()
    alert_engine.basis_active.clear()
    async with httpx.AsyncClient(timeout=15, follow_redirects=True, headers={"User-Agent": "Mozilla/5.0", "Referer": "https://finance.yahoo.com/"}, limits=httpx.Limits(max_connections=12)) as client:
        # Bulk global-market queries must not starve the four spread quote legs.
        _basis_client = httpx.AsyncClient(timeout=httpx.Timeout(12,pool=3),follow_redirects=True,
                                        headers={'User-Agent':'Mozilla/5.0'},limits=httpx.Limits(max_connections=4,max_keepalive_connections=4))
        _basis_history_client = httpx.AsyncClient(timeout=httpx.Timeout(18,pool=3),follow_redirects=True,
                                                headers={'User-Agent':'Mozilla/5.0'},limits=httpx.Limits(max_connections=3,max_keepalive_connections=3))
        _client = client
        # Push delivery must not wait behind global-market/history requests.
        _notification_client = httpx.AsyncClient(timeout=httpx.Timeout(15,connect=10,pool=3),
                                                limits=httpx.Limits(max_connections=3,max_keepalive_connections=2))
        task = asyncio.create_task(_poll_loop())
        delivery_task = asyncio.create_task(_alert_delivery_loop())
        try:
            yield
        finally:
            for background_task in (task, delivery_task):
                background_task.cancel()
            for background_task in (task, delivery_task):
                try:
                    await background_task
                except asyncio.CancelledError:
                    pass
            _client = None
            await _notification_client.aclose()
            _notification_client = None
            await _basis_client.aclose()
            await _basis_history_client.aclose()
            _basis_client = _basis_history_client = None

app = FastAPI(lifespan=lifespan, title="金融资产监控看板", version="2.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.exception_handler(RequestValidationError)
async def alert_validation_error(request: Request,exc: RequestValidationError):
    if request.url.path.startswith("/api/alerts/wechat"):
        return JSONResponse(status_code=422,content={"detail":"微信设置格式不正确，请检查开关和SendKey长度"})
    return await request_validation_exception_handler(request,exc)


@app.middleware("http")
async def protect_alert_settings(request: Request,call_next):
    if request.url.path.startswith("/api/alerts") and request.method not in ("GET","HEAD","OPTIONS"):
        origin=request.headers.get("origin")
        if origin:
            parsed=urlparse(origin)
            same_host=parsed.netloc==request.headers.get("host")
            try:
                dev_origin=(request.url.hostname in ("localhost","127.0.0.1") and
                            parsed.hostname==request.url.hostname and parsed.port==19191)
            except ValueError:
                dev_origin=False
            if parsed.scheme not in ("http","https") or not (same_host or dev_origin):
                return JSONResponse(status_code=403,content={"detail":"请从本站页面修改预警和通知设置"})
    return await call_next(request)


# ── 持久化 ─────────────────────────────────────────────────
WATCHLIST_FILE = Path(os.environ.get("WATCHLIST_FILE", str(Path(__file__).parent / "custom_watchlist.json")))
basis_service = BasisService(WATCHLIST_FILE.parent / 'basis_snapshot.json')
alert_engine = AlertEngine(WATCHLIST_FILE.parent / "alerts_state.json")
_alert_queue = asyncio.Queue(maxsize=200)

# ── 市场代码映射 ───────────────────────────────────────────
SYMBOL_ALIAS = {
    "ASTER": "ASTERUSDT",
    "XAUUSD": "hf_GC",
    "XAUUSDT": "hf_GC",
    "XAGUSD": "hf_SI",
    "XAGUSDT": "hf_SI",
    "GOLD": "hf_GC",
}

SINA_PREFIX = {
    "6": "sh", "9": "sh",
    "0": "sz", "2": "sz", "3": "sz",
    "4": "sz", "5": "sz", "8": "sz",
}

CRYPTO_SYMBOLS = {
    "ASTERUSDT", "BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT", "ADAUSDT",
    "DOGEUSDT", "DOTUSDT", "MATICUSDT", "SHIBUSDT", "TRXUSDT",
    "AVAXUSDT", "UNIUSDT", "ATOMUSDT", "LINKUSDT", "ETCUSDT",
    "FILUSDT", "NEARUSDT", "APTUSDT", "LTCUSDT", "BCHUSDT",
    "ICPUSDT", "OPUSDT", "SUIUSDT", "ARBUSDT", "PEPEUSDT",
}


# ── 连接管理器 ────────────────────────────────────────────
class ConnectionManager:
    def __init__(self):
        self.active: list[WebSocket] = []

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self.active.append(ws)

    def disconnect(self, ws: WebSocket):
        if ws in self.active:
            self.active.remove(ws)

    async def broadcast(self, data: dict):
        stale = []
        for ws in self.active:
            try:
                await ws.send_json(data)
            except Exception:
                stale.append(ws)
        for ws in stale:
            self.disconnect(ws)


manager = ConnectionManager()


# ── 工具函数 ───────────────────────────────────────────────
def _load_json(path: str, default: list) -> list:
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def _save_json(path: str, data: list):
    tmp = Path(str(path) + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def _normalise(symbol: str) -> str:
    return symbol.upper().replace("_", "")


def _identify_market(symbol: str) -> str:
    s = symbol.upper()
    if contract_info(s):
        return "futures"
    if s not in ("XAUUSDT", "XAGUSDT") and (s in CRYPTO_SYMBOLS or s == "ASTER" or re.fullmatch(r"[A-Z0-9]{2,16}USDT", s)):
        return "crypto"
    if s.startswith("HF_") or s in ("XAUUSD", "XAGUSD", "XAUUSDT", "XAGUSDT"):
        return "metal"
    if symbol.startswith(("6", "9", "0", "2", "3", "4", "5", "8")) and len(symbol) == 6:
        return "a-stock"
    if s in ("HSTECH", "HSI", "HSCEI"):
        return "index"
    if symbol.isdigit() and len(symbol) == 5:
        return "hk-stock"
    if symbol.isalpha() and len(symbol) <= 4:
        return "us-stock"
    return "unknown"


async def _fetch_sina(symbol: str) -> Optional[dict]:
    """通过共享 HTTP 连接获取新浪财经行情。"""
    s_upper = symbol.upper()
    prefix = ""
    raw = symbol

    if s_upper.startswith("HF_"):
        prefix = "hf_"
        raw = s_upper[3:]
    elif s_upper in SINA_PREFIX:
        # 6位纯数字A股
        prefix = SINA_PREFIX[s_upper]
        raw = symbol
    elif len(s_upper) == 6 and s_upper[0] in SINA_PREFIX:
        prefix = SINA_PREFIX[s_upper[0]]
        raw = symbol
    elif s_upper in ("HSTECH", "HSI", "HSCEI"):
        prefix = "rt_hk"
        raw = s_upper
    elif s_upper.isdigit() and len(s_upper) == 5:
        # 港股
        prefix = "rt_hk"
        raw = s_upper
    elif s_upper.isalpha() and len(s_upper) <= 4:
        # 美股
        prefix = "gb_"
        raw = s_upper.lower()
    else:
        prefix = "qb"
        raw = s_upper

    url = f"http://hq.sinajs.cn/list={prefix}{raw}"
    try:
        resp = await _client.get(url, headers={"Referer": "https://finance.sina.com.cn"})
        resp.raise_for_status()
        text = resp.content.decode("gbk", errors="ignore")
        return _parse_sina(symbol, text)
    except Exception:
        logger.warning("获取 %s 行情失败", symbol, exc_info=True)
        return None


def _parse_sina(symbol: str, text: str) -> Optional[dict]:
    """解析新浪财经返回的 CSV 数据"""
    match = re.search(r'"(.*)"', text)
    if not match:
        return None
    parts = match.group(1).split(",")
    if len(parts) < 12:
        return None

    # 判断是否为美股
    is_us = "gb_" in text[:50].lower()
    # 真·港股: 5位数字 (不含指数)
    is_hk = symbol.isdigit() and len(symbol) == 5
    # 港股指数
    is_hk_index = symbol.upper() in ("HSTECH", "HSI", "HSCEI")

    name = parts[0]
    open_p = close_p = price = high = low = None

    if is_us:
        # 美股: name,price,change,datetime,?,open,high,low,52wH,52wL,...
        try:
            price = float(parts[1]) if parts[1] else None
            change_val = float(parts[4]) if parts[4] else 0
            close_p = price - change_val if price else None   # 昨收 = 现价 - 涨跌额
            open_p = float(parts[5]) if parts[5] else None
            high = float(parts[6]) if parts[6] else None
            low = float(parts[7]) if parts[7] else None
        except (ValueError, IndexError):
            pass
    elif is_hk_index:
        try:
            name = parts[1] if len(parts) > 1 and parts[1] else parts[0]
            open_p = float(parts[2]) if parts[2] else None
            close_p = float(parts[3]) if parts[3] else None
            high = float(parts[4]) if parts[4] else None
            low = float(parts[5]) if parts[5] else None
            price = float(parts[6]) if parts[6] else None
        except (ValueError, IndexError):
            pass
    elif is_hk:
        # 港股: name,中文名,price,high,low,prev_close,...
        try:
            name = parts[1] if len(parts) > 1 and parts[1] else parts[0]
            open_p = float(parts[2]) if parts[2] else None
            close_p = float(parts[3]) if parts[3] else None
            high = float(parts[4]) if parts[4] else None
            low = float(parts[5]) if parts[5] else None
            price = float(parts[6]) if parts[6] else None
        except (ValueError, IndexError):
            pass
    elif len(parts) >= 30:
        # A股
        try:
            open_p = float(parts[1]) if parts[1] else None
            close_p = float(parts[2]) if parts[2] else None
            price = float(parts[3]) if parts[3] else None
            high = float(parts[4]) if parts[4] else None
            low = float(parts[5]) if parts[5] else None
        except (ValueError, IndexError):
            pass
    else:
        # 期货/贵金属
        try:
            price = float(parts[0]) if parts[0] else None
            close_p = float(parts[7]) if parts[7] else None
            open_p = float(parts[8]) if parts[8] else None
            high = float(parts[4]) if parts[4] else None
            low = float(parts[5]) if parts[5] else None
        except (ValueError, IndexError):
            pass
        # 期货名称在最后几列
        name = parts[13] if len(parts) > 13 and parts[13] else symbol

    if price is None or price == 0:
        return None

    change = price - close_p if close_p else 0
    change_pct = (change / close_p * 100) if close_p and close_p != 0 else 0

    return {
        "symbol": symbol,
        "name": name,
        "price": round(price, 4),
        "change": round(change, 4),
        "changePct": round(change_pct, 2),
        "high": round(high, 4) if high else None,
        "low": round(low, 4) if low else None,
        "open": round(open_p, 4) if open_p else None,
        "close": round(close_p, 4) if close_p else None,
        "market": _identify_market(symbol),
        "source": "sina",
        "updated": datetime.now(timezone.utc).isoformat(),
    }


async def _fetch_binance(symbol: str) -> Optional[dict]:
    """通过 Binance API 获取加密货币行情"""
    s = symbol.upper().replace("_", "")
    url = f"https://data-api.binance.vision/api/v3/ticker/24hr?symbol={s}"
    try:
        resp = await _client.get(url)
        resp.raise_for_status()
        data = resp.json()
        price = float(data["lastPrice"])
        change = float(data["priceChange"])
        change_pct = float(data["priceChangePercent"])
        high = float(data["highPrice"])
        low = float(data["lowPrice"])
        open_p = float(data["openPrice"])
        return {
            "symbol": symbol,
            "name": s,
            "price": round(price, 6),
            "change": round(change, 6),
            "changePct": round(change_pct, 2),
            "high": round(high, 6),
            "low": round(low, 6),
            "open": round(open_p, 6),
            "close": round(open_p, 6),
            "market": "crypto",
            "source": "binance",
            "updated": datetime.now(timezone.utc).isoformat(),
        }
    except Exception:
        logger.warning("获取 %s 行情失败", symbol, exc_info=True)
        return None


async def fetch_price(symbol: str) -> Optional[dict]:
    """智能路由：根据符号选择数据源"""
    sym = symbol.strip()
    future = contract_info(sym)
    if future:
        return await fetch_futures_quote(_client, future)
    # 别名映射
    alias = SYMBOL_ALIAS.get(sym.upper())
    if alias:
        sym = alias

    market = _identify_market(sym)

    result = await (_fetch_binance(sym) if market == "crypto" else _fetch_sina(sym))
    if result:
        result["symbol"] = symbol.strip().upper()
    return result


async def fetch_prices(symbols: list[str]) -> list[dict]:
    """批量获取行情（并发）"""
    tasks = [fetch_price(s) for s in symbols]
    results = await asyncio.gather(*tasks)
    return [r for r in results if r is not None]


# ── K 线数据 ───────────────────────────────────────────────
def kline_info(symbol: str) -> dict:
    symbol = symbol.strip().upper()
    mapped = SYMBOL_ALIAS.get(symbol, symbol).upper()
    market = _identify_market(mapped)
    intervals = ["1m", "5m", "15m", "1h", "4h", "1d"]
    notice = ""
    if market == "futures":
        provider, source = symbol, "yahoo"
        if contract_info(symbol).get('region') == '中国内地':
            source, intervals = 'sina', ['1m','5m','15m','1h','1d']
        notice = ("连续期货会自动换月，可能产生跳空；不是固定交割月合约。"
                  if contract_info(symbol)["contractType"] == "continuous"
                  else "固定交割月合约；到期后不会自动换月，请另行添加下一合约。")
        notice += " 公开报价可能延迟，最后一根K线可能尚未收盘。"
        if source == 'sina':
            notice += ' 国内期货涨跌幅相对昨结算价；K线时间为北京时间。'
    elif market == "crypto":
        provider, source = mapped, "binance"
    elif market == "a-stock":
        suffix = ".SS" if symbol[0] in "569" else ".BJ" if symbol[0] in "48" else ".SZ"
        provider, source = symbol + suffix, "yahoo"
    elif market == "hk-stock":
        provider, source = symbol.lstrip("0").zfill(4) + ".HK", "yahoo"
    elif market == "us-stock":
        provider, source = symbol, "yahoo"
    elif symbol == "HSTECH":
        provider, source, intervals = "hkHSTECH", "tencent", ["1d"]
        notice = "恒生科技指数当前提供真实日线；成交量栏为成交额。"
    elif market == "index":
        provider, source = {"HSI": "^HSI", "HSCEI": "^HSCE"}[symbol], "yahoo"
    elif market == "metal" and mapped in ("HF_GC", "HF_SI"):
        provider, source = {"HF_GC": "GC=F", "HF_SI": "SI=F"}[mapped], "yahoo"
        notice = "历史走势为COMEX黄金/白银连续期货，并非现货报价；换月可能产生跳空。"
    else:
        raise HTTPException(status_code=422, detail="该标的暂不支持历史K线")
    return {"source": source, "providerSymbol": provider, "supportedIntervals": intervals,
            "notice": notice, "volumeType": "turnover" if source == "tencent" else "volume"}


_kline_cache = {}
_kline_locks = {}

async def fetch_kline(symbol: str, interval: str = "1h", limit: int = 100) -> list:
    info = kline_info(symbol)
    if interval not in info["supportedIntervals"]:
        raise HTTPException(status_code=422, detail="该标的不支持此周期，请选择：" + ", ".join(info["supportedIntervals"]))
    key = (symbol.upper(), interval, limit)
    if key not in _kline_locks:
        if len(_kline_locks) >= 256:
            # 锁由本次调用持有引用；只清理未在使用的条目。
            for old in list(_kline_locks):
                if not _kline_locks[old].locked():
                    _kline_locks.pop(old)
                    _kline_cache.pop(old, None)
        _kline_locks[key] = asyncio.Lock()
    async with _kline_locks[key]:
        cached = _kline_cache.get(key)
        if cached and time.monotonic() - cached[0] < 30:
            return cached[1]
        try:
            if info["source"] == "sina":
                data = await domestic_futures.fetch_kline(_client, info['providerSymbol'], interval)
            elif info["source"] == "binance":
                data = await _fetch_kline_binance(info["providerSymbol"], interval, limit)
            elif info["source"] == "tencent":
                data = await _fetch_kline_tencent(info["providerSymbol"], limit)
            else:
                data = await _fetch_kline_yahoo(info["providerSymbol"], interval, limit)
            data = _clean_candles(data)[-limit:]
        except (httpx.HTTPError, ValueError, KeyError, TypeError, IndexError) as exc:
            logger.warning("历史行情源请求失败: %s %s", symbol, interval, exc_info=True)
            raise HTTPException(status_code=502, detail="历史行情源暂不可用，请稍后重试") from exc
        if not data:
            raise HTTPException(status_code=404, detail="该周期暂无有效历史行情，请切换周期")
        _kline_cache[key] = (time.monotonic(), data)
        return data


def _clean_candles(rows: list) -> list:
    candles = {}
    for row in rows:
        try:
            candle = {"time": int(row["time"]), **{k: float(row[k]) for k in ("open", "high", "low", "close")},
                      "volume": float(row.get("volume") or 0)}
            if (candle["time"] <= 0 or not all(math.isfinite(v) for v in candle.values())
                or min(candle[k] for k in ("open", "high", "low", "close")) <= 0
                or candle["volume"] < 0
                or candle["high"] < max(candle["open"], candle["close"], candle["low"])
                or candle["low"] > min(candle["open"], candle["close"])):
                continue
            candles[candle["time"]] = candle
        except (ValueError, TypeError, KeyError, OverflowError):
            continue
    return [candles[t] for t in sorted(candles)]


def _aggregate_four_hours(rows: list) -> list:
    buckets = {}
    for row in _clean_candles(rows):
        start = row["time"] // 14400 * 14400
        if start not in buckets:
            buckets[start] = {**row, "time": start}
        else:
            bar = buckets[start]
            bar["high"] = max(bar["high"], row["high"])
            bar["low"] = min(bar["low"], row["low"])
            bar["close"] = row["close"]
            bar["volume"] += row["volume"]
    return list(buckets.values())


async def _fetch_kline_yahoo(symbol: str, interval: str, limit: int) -> list:
    provider_interval = "60m" if interval in ("1h", "4h") else interval
    period = {"1m": "7d", "5m": "1mo", "15m": "1mo", "1h": "2y", "4h": "2y", "1d": "10y"}[interval]
    params = {"interval": provider_interval, "range": period, "includePrePost": "false"}
    payload = None
    for host in ("query1.finance.yahoo.com", "query2.finance.yahoo.com"):
        try:
            response = await _client.get(f"https://{host}/v8/finance/chart/{symbol}", params=params)
            response.raise_for_status()
            payload = response.json()["chart"]
            if payload.get("error"):
                raise ValueError("Yahoo returned an error")
            break
        except (httpx.HTTPError, ValueError, KeyError):
            if host.startswith("query2"):
                raise
    result = payload["result"][0]
    timestamps = result.get("timestamp") or []
    quotes = result["indicators"]["quote"][0]
    rows = []
    for i, ts in enumerate(timestamps):
        row = {"time": ts}
        for field in ("open", "high", "low", "close", "volume"):
            values = quotes.get(field) or []
            row[field] = values[i] if i < len(values) else None
        rows.append(row)
    rows = _clean_candles(rows)
    return (_aggregate_four_hours(rows) if interval == "4h" else rows)[-limit:]


async def _fetch_kline_tencent(symbol: str, limit: int) -> list:
    response = await _client.get("https://web.ifzq.gtimg.cn/appstock/app/fqkline/get",
                                 params={"param": f"{symbol},day,,,{limit},qfq"})
    response.raise_for_status()
    payload = response.json()
    if payload.get("code") != 0:
        raise ValueError("Tencent returned an error")
    rows = payload["data"][symbol].get("day", [])
    result = []
    for row in rows:
        if len(row) < 6:
            continue
        # 日线以交易日00:00 UTC编码，避免本机时区影响。
        result.append({"time": int(datetime.strptime(row[0], "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp()),
                       "open": row[1], "close": row[2], "high": row[3], "low": row[4], "volume": row[5]})
    return result


INTERVAL_MAP = {
    "1m": "1m", "5m": "5m", "15m": "15m",
    "1h": "1h", "4h": "4h", "1d": "1d",
}


async def _fetch_kline_binance(symbol: str, interval: str, limit: int) -> list:
    s = symbol.upper().replace("_", "")
    mapped = INTERVAL_MAP.get(interval, "1h")
    url = f"https://data-api.binance.vision/api/v3/klines?symbol={s}&interval={mapped}&limit={limit}"
    resp = await _client.get(url)
    resp.raise_for_status()
    data = resp.json()
    return [{"time": int(k[0]) // 1000, "open": k[1], "high": k[2], "low": k[3],
             "close": k[4], "volume": k[5]} for k in data]


# ── 标的池管理 ─────────────────────────────────────────────
def _get_watchlist() -> list[dict]:
    return _load_json(WATCHLIST_FILE, [])


def _save_watchlist(data: list[dict]):
    _save_json(WATCHLIST_FILE, data)


# ── 后端轮询任务 ──────────────────────────────────────────
_last_prices: dict[str, dict] = {}


async def _refresh_prices():
    async with _refresh_lock:
        symbols = [item["symbol"] for item in _get_watchlist()]
        now = time.monotonic()
        missing = [s for s in symbols if now - _price_times.get(_normalise(s), 0) >= 15]
        if missing:
            prices = await fetch_prices(missing)
            for p in prices:
                key = _normalise(p["symbol"])
                _last_prices[key] = p
                _price_times[key] = time.monotonic()
            for event in alert_engine.observe(prices):
                try:
                    _alert_queue.put_nowait(event)
                except asyncio.QueueFull:
                    alert_engine.delivery_result(event["id"], "failed", "通知队列繁忙，请查看告警历史")
        return [_last_prices[_normalise(s)] for s in symbols if _normalise(s) in _last_prices]

_price_times = {}

async def _poll_loop():
    while True:
        try:
            prices = await _refresh_prices()
            await manager.broadcast({"type": "prices", "data": prices})
            if any(rule['enabled'] for rule in alert_engine.state['basisRules']):
                snapshot = await basis_service.current(_basis_client)
                for event in alert_engine.observe_basis(snapshot['items']):
                    try:
                        _alert_queue.put_nowait(event)
                    except asyncio.QueueFull:
                        alert_engine.delivery_result(event['id'], 'failed', '通知队列繁忙')
        except Exception:
            logger.exception("行情刷新失败")
        await asyncio.sleep(15)


async def _alert_delivery_loop():
    while True:
        event = await _alert_queue.get()
        try:
            await manager.broadcast({"type": "alert", "data": event})
            status, error = await alert_engine.send_wechat(_notification_client, event)
            alert_engine.delivery_result(event["id"], status, error)
            await manager.broadcast({"type": "alert_delivery", "data": {"id":event["id"],"wechatStatus":status,"wechatError":error}})
        except asyncio.CancelledError:
            raise
        except Exception:
            alert_engine.delivery_result(event["id"], "failed", "通知处理失败，请查看告警历史")
        finally:
            _alert_queue.task_done()


# ── REST API ────────────────────────────────────────────────
@app.get("/api/health")
async def health():
    return {
        "status": "ok",
        "time": datetime.now(timezone.utc).isoformat(),
        "watchlist_count": len(_get_watchlist()),
    }


class AlertRuleBody(BaseModel):
    symbol: str = Field(min_length=1,max_length=20)
    windowMinutes: int = Field(default=5,ge=1,le=1440,strict=True)
    thresholdPct: float = Field(default=1,ge=0.01,le=1000,allow_inf_nan=False)
    direction: str = Field(default="both",pattern=r"^(both|up|down)$")
    cooldownMinutes: int = Field(default=5,ge=1,le=1440,strict=True)

class AlertToggleBody(BaseModel):
    enabled: bool = Field(strict=True)


class BasisRuleBody(BaseModel):
    symbol: str = Field(pattern=r'^(IC|IM)$')
    operator: str = Field(pattern=r'^(lte|gte)$')
    thresholdPct: float = Field(ge=-100, le=100, allow_inf_nan=False)
    cooldownMinutes: int = Field(default=60, ge=1, le=1440, strict=True)


@app.get('/api/basis')
async def get_basis():
    return await basis_service.current(_basis_client)


@app.get('/api/basis/history')
async def get_basis_history():
    return await basis_service.history(_basis_history_client)


@app.post('/api/alerts/basis-rules')
async def create_basis_alert(body: BasisRuleBody):
    try:return alert_engine.add_basis_rule(body.model_dump())
    except ValueError as exc:raise HTTPException(status_code=400,detail=str(exc)) from exc


@app.patch('/api/alerts/basis-rules/{rid}')
async def toggle_basis_alert(rid: str, body: AlertToggleBody):
    try:alert_engine.change_basis_rule(rid,enabled=body.enabled)
    except KeyError:raise HTTPException(status_code=404,detail='贴水预警不存在')
    return {'status':'updated'}


@app.delete('/api/alerts/basis-rules/{rid}')
async def delete_basis_alert(rid: str):
    try:alert_engine.change_basis_rule(rid,delete=True)
    except KeyError:raise HTTPException(status_code=404,detail='贴水预警不存在')
    return {'status':'deleted'}

class WechatBody(AlertToggleBody):
    sendKey: str = Field(default="",max_length=210)

@app.get("/api/alerts")
async def get_alerts():
    return alert_engine.public_state()

@app.post("/api/alerts/rules")
async def create_alert(body: AlertRuleBody):
    symbol=body.symbol.strip().upper()
    entry=next((i for i in _get_watchlist() if i["symbol"].upper()==symbol),None)
    if entry is None:
        raise HTTPException(status_code=400,detail="请先将该标的加入自选")
    try:
        return alert_engine.add_rule({**body.model_dump(),"symbol":symbol,"name":entry["name"],"enabled":True})
    except ValueError as exc:
        raise HTTPException(status_code=400,detail=str(exc)) from exc

@app.patch("/api/alerts/rules/{rule_id}")
async def toggle_alert(rule_id: str,body: AlertToggleBody):
    try:return alert_engine.toggle_rule(rule_id,body.enabled)
    except KeyError:raise HTTPException(status_code=404,detail="预警规则不存在")

@app.delete("/api/alerts/rules/{rule_id}")
async def delete_alert(rule_id: str):
    try:alert_engine.delete_rule(rule_id)
    except KeyError:raise HTTPException(status_code=404,detail="预警规则不存在")
    return {"status":"deleted"}

@app.post("/api/alerts/wechat")
async def configure_wechat(body: WechatBody):
    try:alert_engine.configure_wechat(body.enabled,body.sendKey.strip())
    except ValueError as exc:raise HTTPException(status_code=400,detail=str(exc)) from exc
    return alert_engine.public_state()["wechat"]

@app.post("/api/alerts/wechat/test")
async def test_wechat():
    status,error=await alert_engine.send_wechat(_notification_client,test=True)
    alert_engine.state['wechat']['lastTest']={'status':status,'error':error,'time':datetime.now(timezone.utc).isoformat()}
    alert_engine.save()
    if status!="sent":raise HTTPException(status_code=400,detail=error)
    return {"status":"sent"}


@app.get("/api/global-markets")
async def global_markets():
    return await get_global_markets(_client)


@app.get("/api/prices")
async def get_all_prices():
    symbols = [item["symbol"] for item in _get_watchlist()]
    if not symbols:
        return []
    return await _refresh_prices()


@app.get("/api/prices/{symbol:path}")
async def get_price(symbol: str):
    norm = _normalise(symbol)
    if norm in _last_prices and time.monotonic() - _price_times.get(norm, 0) < 15:
        return _last_prices[norm]
    result = await fetch_price(symbol)
    if not result:
        raise HTTPException(status_code=404, detail=f"无法获取 {symbol} 的行情")
    return result


# 名称搜索结果仅用于选择，添加时仍验证实际行情。
_SEARCH_BUILTINS = [
    {"symbol": "XAUUSD", "name": "黄金", "market": "metal", "aliases": "黄金 金价 现货黄金 gold xauusd"},
    {"symbol": "XAGUSD", "name": "白银", "market": "metal", "aliases": "白银 银价 silver xagusd"},
    {"symbol": "HSI", "name": "恒生指数", "market": "index", "aliases": "恒生指数 hsi"},
    {"symbol": "HSTECH", "name": "恒生科技指数", "market": "index", "aliases": "恒生科技 恒科 hstech"},
    {"symbol": "HSCEI", "name": "恒生中国企业指数", "market": "index", "aliases": "国企指数 hscei"},
]
_CRYPTO_NAMES = {"ASTERUSDT": "Aster", "BTCUSDT": "比特币", "ETHUSDT": "以太坊", "SOLUSDT": "索拉纳", "DOGEUSDT": "狗狗币", "BNBUSDT": "币安币", "XRPUSDT": "瑞波币", "ADAUSDT": "艾达币", "LTCUSDT": "莱特币"}
_search_cache = {}


def _parse_symbol_suggestions(text: str) -> list:
    match = re.search(r'="(.*)"', text, re.S)
    if not match:
        raise ValueError("Invalid suggestion response")
    results = []
    for row in match.group(1).split(";"):
        parts = row.split(",")
        if len(parts) < 5 or (len(parts) > 8 and parts[8] == "0"):
            continue
        symbol = parts[2].upper()
        market = _identify_market(symbol)
        if parts[1] in ("11", "12") and market == "a-stock":
            # 搜索源也可能返回指数；排除与现有报价路由不一致的证券。
            if parts[3].lower() != SINA_PREFIX.get(symbol[0], "") + symbol:
                continue
        elif not ((parts[1] == "31" and market == "hk-stock") or
                  (parts[1] == "41" and market == "us-stock")):
            continue
        name = parts[4].strip()
        if name:
            results.append({"symbol": symbol, "name": name, "market": market})
    return results


@app.get("/api/search")
async def search_symbols(q: str = Query(..., min_length=1, max_length=40)):
    query = q.strip().casefold()
    if not query:
        raise HTTPException(status_code=400, detail="请输入名称或代码")
    local = _get_watchlist() + _SEARCH_BUILTINS + search_contracts(query) + [
        {"symbol": symbol, "name": _CRYPTO_NAMES.get(symbol, symbol), "market": "crypto"}
        for symbol in sorted(CRYPTO_SYMBOLS)
    ]
    matches = [dict(symbol=i["symbol"], name=i["name"], market=i["market"],
                    **({"contractMonth": i.get("contractMonth"), "contractType": i["contractType"]}
                       if i.get("contractType") else {})) for i in local
               if query in (i["symbol"] + " " + i["name"] + " " + i.get("aliases", "")).casefold()]
    unavailable = False
    try:
        cached = _search_cache.get(query)
        if cached and time.monotonic() - cached[0] < 300:
            remote = cached[1]
        else:
            response = await _client.get(f"https://suggest3.sinajs.cn/suggest/type=11,12,31,41&key={quote(q.strip(), safe='')}&name=suggest",
                                         timeout=6,
                                         headers={"Referer": "https://finance.sina.com.cn/"})
            response.raise_for_status()
            remote = _parse_symbol_suggestions(response.content.decode("gbk", errors="replace"))
            if len(_search_cache) >= 128:
                _search_cache.pop(next(iter(_search_cache)))
            _search_cache[query] = (time.monotonic(), remote)
        matches.extend(remote)
    except (httpx.HTTPError, ValueError):
        unavailable = True
        logger.warning("名称搜索源暂不可用")
    unique = {}
    for item in matches:
        unique.setdefault(_normalise(item["symbol"]), item)
    items = sorted(unique.values(), key=lambda i: (i["name"].casefold() != query and i["symbol"].casefold() != query,
                                                  query not in i["name"].casefold(), i["market"],
                                                  i.get("contractMonth") or "", i["symbol"]))[:12]
    if unavailable and not items:
        raise HTTPException(status_code=502, detail="名称搜索暂不可用，请稍后重试或直接输入代码")
    return {"query": q.strip(), "items": items, "partial": unavailable}


@app.get("/api/custom")
async def list_custom():
    return _get_watchlist()


@app.post("/api/custom")
async def add_custom(body: dict):
    symbol = body.get("symbol", "")
    if not isinstance(symbol, str):
        raise HTTPException(status_code=400, detail="代码必须为文本")
    symbol = symbol.strip().upper()
    if symbol == "ASTER":
        symbol = "ASTERUSDT"
    name = body.get("name", "")
    if not isinstance(name, str):
        raise HTTPException(status_code=400, detail="名称必须为文本")
    name = name.strip()
    if not (re.fullmatch(r"[A-Z0-9_]{1,20}", symbol) or contract_info(symbol)) or len(name) > 30:
        raise HTTPException(status_code=400, detail="代码或名称格式不正确")
    if not symbol:
        raise HTTPException(status_code=400, detail="代码不能为空")

    existing = {_normalise(item["symbol"]) for item in _get_watchlist()}
    if _normalise(symbol) in existing:
        raise HTTPException(status_code=409, detail=f"{symbol} 已存在")

    price_data = await fetch_price(symbol)
    if not price_data:
        raise HTTPException(status_code=400, detail=f"无法获取 {symbol} 的行情，请检查代码是否正确")

    entry = {
        "symbol": symbol,
        "name": name or price_data.get("name", symbol),
        "market": price_data.get("market", _identify_market(symbol)),
        "added": datetime.now(timezone.utc).isoformat(),
    }
    if contract_info(symbol):
        entry.update({k: v for k, v in contract_info(symbol).items() if k != "aliases"})
        entry["name"] = name or price_data["name"]
    async with _watchlist_lock:
        watchlist = _get_watchlist()
        if any(_normalise(i["symbol"]) == _normalise(symbol) for i in watchlist):
            raise HTTPException(status_code=409, detail=f"{symbol} 已存在")
        watchlist.append(entry)
        _save_watchlist(watchlist)

    return {"entry": entry, "price": price_data}


@app.delete("/api/custom/{symbol:path}")
async def delete_custom(symbol: str):
    watchlist = _get_watchlist()
    new_list = [item for item in watchlist if _normalise(item["symbol"]) != _normalise(symbol)]
    if len(new_list) == len(watchlist):
        raise HTTPException(status_code=404, detail=f"{symbol} 不在标的池中")
    _save_watchlist(new_list)
    alert_engine.remove_symbol(symbol.strip().upper())
    _last_prices.pop(_normalise(symbol), None)
    _price_times.pop(_normalise(symbol), None)
    return {"status": "deleted", "symbol": symbol}


@app.get("/api/kline/{symbol:path}")
async def get_kline(symbol: str, interval: str = Query("1h", pattern=r"^(1m|5m|15m|1h|4h|1d)$"), limit: int = Query(100, ge=2, le=1000)):
    data = await fetch_kline(symbol, interval, limit)
    if not data:
        raise HTTPException(status_code=404, detail=f"无法获取 {symbol} 的K线数据")
    return {"symbol": symbol, "interval": interval, "data": data, **kline_info(symbol),
            "aggregation": "UTC 4小时窗口，由真实小时K线聚合；最后一根可能尚未收盘" if interval == "4h" else "最后一根可能尚未收盘"}


# ── WebSocket ──────────────────────────────────────────────
@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await manager.connect(ws)
    try:
        while True:
            try:
                data = await asyncio.wait_for(ws.receive_text(), timeout=30)
                msg = json.loads(data)
                if isinstance(msg, dict) and msg.get("action") == "ping":
                    await ws.send_json({"type": "pong"})
            except asyncio.TimeoutError:
                try:
                    await ws.send_json({"type": "heartbeat"})
                except Exception:
                    break
            except (WebSocketDisconnect, json.JSONDecodeError):
                break
    except WebSocketDisconnect:
        pass
    finally:
        manager.disconnect(ws)


# ── 入口 ───────────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=9090)
