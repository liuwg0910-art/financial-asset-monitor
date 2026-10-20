/* ═══════════════════════════════════════════════════════════
   金融资产监控看板
   ═══════════════════════════════════════════════════════════ */

const API = {
  base: location.port === "19191" && ["localhost", "127.0.0.1"].includes(location.hostname) ? `${location.protocol}//${location.hostname}:9090` : location.origin,
  get ws() { return this.base.replace(/^http/, "ws") + "/ws"; },
  get prices() { return this.base + "/api/prices"; },
  custom() { return this.base + "/api/custom"; },
  kline(sym) { return this.base + "/api/kline/" + encodeURIComponent(sym); },
};

// ── State ────────────────────────────────────────────────
let symbolMap = {};
let priceMap = {};
const priceHistory = {};
function storePrice(p) {
  const key = N(p.symbol);
  const history = priceHistory[key] || (priceHistory[key] = []);
  if (priceMap[key]?.updated !== p.updated) { history.push(p.price); if (history.length > 40) history.shift(); }
  priceMap[key] = p;
}
let currentFilter = "all";
let ws = null;
let wsReconnectTimer = null;
let chartInstance = null;
let modalSymbol = null;

// ── DOM refs ─────────────────────────────────────────────
const $ = s => document.querySelector(s);
const $$ = s => document.querySelectorAll(s);

const grid = $("#cardGrid");
const list = $("#symbolList");
const badge = $("#countBadge");
const symInp = $("#inputSymbol");
const nameInp = $("#inputName");
const filterInp = $("#inputFilter");
const connDot = $("#connStatus .status-dot");
const connLbl = $("#connStatus .status-label");
const clock = $("#priceTime");
const backdrop = $("#modalOverlay");
const modTitle = $("#modalTitle");
const modStats = $("#modalStats");
const chartLoading = $("#chartLoading");
const crossEl = $("#crosshairInfo");

// ── Init ─────────────────────────────────────────────────
async function init() {
  await loadWatchlist();
  renderList();
  renderGrid();
  await initAlerts();
  connectWS();
  startPolling();
  startGlobalMarkets();

  $("#btnRefresh")?.addEventListener("click", () => { startPolling(); loadGlobalMarkets(); toast("正在同步最新行情"); });
  document.addEventListener("keydown", e => { if (e.key === "Escape" && modalSymbol) closeModal(); });
  $("#btnAdd").addEventListener("click", addSymbol);
  symInp.addEventListener("keydown", e => e.key === "Enter" && addSymbol());

  filterInp?.addEventListener("input", applyFilter);

  $$(".chip").forEach(btn => {
    btn.addEventListener("click", () => {
      $$(".chip").forEach(b => b.classList.remove("is-active"));
      btn.classList.add("is-active");
      currentFilter = btn.dataset.filter;
      applyFilter();
    });
  });

  $$(".intv").forEach(btn => {
    btn.addEventListener("click", async () => {
      $$(".intv").forEach(b => b.classList.remove("is-active"));
      btn.classList.add("is-active");
      if (modalSymbol) await loadKline(modalSymbol, btn.dataset.interval);
    });
  });

  document.addEventListener("click", e => {
    if (!e.target.closest(".symbol-search")) hideSymbolSuggestions();
  });
  symInp.addEventListener("keydown", e => {
    if (e.key === "Escape") { hideSymbolSuggestions(); cancelSymbolSearch(); }
    if (e.key === "ArrowDown") { e.preventDefault(); $("#symbolSuggestions button")?.focus(); }
  });
  symInp.addEventListener("input", () => {
    clearTimeout(symbolSearchTimer);
    cancelSymbolSearch();
    hideSymbolSuggestions();
    symbolSearchTimer = setTimeout(searchSymbolSuggestions, 300);
    if (nameInp.dataset.autofilled) { nameInp.value = ""; delete nameInp.dataset.autofilled; }
  });
}

// ── API ──────────────────────────────────────────────────
async function api(path, opts = {}) {
  try {
    const r = await fetch(API.base + path, { headers: { "Content-Type": "application/json" }, ...opts });
    if (!r.ok) { const e = await r.json().catch(() => ({})); throw new Error(e.detail || `HTTP ${r.status}`); }
    return await r.json();
  } catch (e) {
    if (e.name !== "TypeError") throw e;
    throw new Error("无法连接到服务器");
  }
}

// ── Watchlist ────────────────────────────────────────────
async function loadWatchlist() {
  try {
    const items = await api("/api/custom");
    symbolMap = {};
    items.filter(i => /^[A-Z0-9_]{1,20}$/i.test(i.symbol) || isFuture(i.symbol)).forEach(i => { symbolMap[N(i.symbol)] = i; });
  } catch (e) {
    symbolMap = {};
    toast(e.message);
  }
}

async function addSymbol() {
  let sym = symInp.value.trim().toUpperCase();
  const name = nameInp.value.trim();
  if (!sym) return toast("请输入名称或代码");
  if ((!/^[A-Z0-9_]{1,20}$/.test(sym) && !isFuture(sym)) || classify(sym) === "unknown") {
    await searchSymbolSuggestions();
    return toast("请先从搜索结果中选择要添加的标的");
  }
  cancelSymbolSearch();
  hideSymbolSuggestions();

  const btn = $("#btnAdd");
  btn.disabled = true; btn.textContent = "验证中";

  try {
    const body = { symbol: sym };
    if (name) body.name = name;
    const r = await api("/api/custom", { method: "POST", body: JSON.stringify(body) });
    symbolMap[N(r.entry.symbol)] = r.entry;
    if (r.price) storePrice(r.price);
    symInp.value = ""; nameInp.value = ""; delete nameInp.dataset.autofilled;
    renderList(); renderGrid();
    updateAlertSymbols();
    toast(`已添加 ${r.entry.name}`);
  } catch (e) { toast(e.message); }
  finally { btn.disabled = false; btn.innerHTML = '添加到自选 <span>＋</span>'; }
}

async function delSymbol(sym, e) {
  e.stopPropagation();
  if (!confirm(`删除 ${sym}？`)) return;
  try {
    await api("/api/custom/" + encodeURIComponent(sym), { method: "DELETE" });
    delete symbolMap[N(sym)]; delete priceMap[N(sym)]; delete priceHistory[N(sym)];
    if (modalSymbol === sym) closeModal();
    renderList(); renderGrid();
    updateAlertSymbols();
    loadAlertState();
    toast(`已删除 ${sym}`);
  } catch (err) { toast(err.message); }
}

// ── Polling ──────────────────────────────────────────────
let pollTimer = null;
let polling = false;
function startPolling() {
  if (pollTimer) clearInterval(pollTimer);
  const poll = async () => {
    const keys = Object.keys(symbolMap);
    if (!keys.length || polling) return;
    polling = true;
    try {
      const r = await fetch(API.prices);
      if (!r.ok) return;
      (await r.json()).forEach(storePrice);
      renderGrid();
      clock.textContent = new Date().toLocaleTimeString("zh-CN", { hour12: false });
      if (modalSymbol) updateStats(modalSymbol);
    } catch (_) {}
    finally { polling = false; }
  };
  poll();
  pollTimer = setInterval(poll, 15000);
}

// ── WebSocket ────────────────────────────────────────────
function connectWS() {
  if (ws) try { ws.close(); } catch(_) {}
  ws = new WebSocket(API.ws);
  ws.onopen = () => { connDot.className = "status-dot"; connLbl.textContent = "已连接"; wsReconnectTimer = null; };
  ws.onmessage = e => {
    try {
      const m = JSON.parse(e.data);
      if (m.type === "alert") receiveAlertEvent(m.data);
      if (m.type === "alert_delivery") updateAlertDelivery(m.data);
      if (m.type === "prices" && Array.isArray(m.data)) {
        m.data.forEach(p => { storePrice(p); });
        renderGrid();
        if (modalSymbol) updateStats(modalSymbol);
      }
    } catch(_) {}
  };
  ws.onclose = () => { connDot.className = "status-dot disconnected"; connLbl.textContent = "已断开"; reconnect(); };
  ws.onerror = () => { ws.close(); };
}

function reconnect() {
  if (wsReconnectTimer) return;
  wsReconnectTimer = setTimeout(() => { wsReconnectTimer = null; connectWS(); }, 3000);
}

// ── Render list ──────────────────────────────────────────
function renderList() {
  const arr = Object.values(symbolMap);
  badge.textContent = arr.length;

  if (!arr.length) {
    list.innerHTML = '<li style="padding:14px 18px;text-align:center;color:var(--text3);font-size:12px;">暂无标的</li>';
    return;
  }

  list.innerHTML = arr.sort((a, b) => a.symbol.localeCompare(b.symbol)).map(i => {
    const act = modalSymbol === i.symbol ? ' class="sym-item is-active"' : ' class="sym-item"';
    return `<li${act} data-sym="${i.symbol}" role="button" tabindex="0" aria-label="查看 ${i.symbol} 行情详情" onkeydown="if(event.target===this && (event.key==='Enter'||event.key===' ')){event.preventDefault();openModal('${i.symbol}')}" onclick="openModal('${i.symbol}')">
      <div><span class="sym-code">${i.symbol}</span><span class="sym-name">${escapeHTML(i.name || "")}</span></div>
      <button class="sym-del" onclick="delSymbol('${i.symbol}', event)">&times;</button>
    </li>`;
  }).join("");
}

// ── Filter helpers ───────────────────────────────────────
function filtered() {
  const q = filterInp.value.trim().toLowerCase();
  return Object.values(symbolMap).filter(i => {
    if (currentFilter !== "all") {
      const m = classify(i.symbol);
      if (currentFilter === "stock" && !["a-stock","us-stock","hk-stock","index"].includes(m)) return false;
      if (currentFilter !== "stock" && m !== currentFilter) return false;
    }
    if (!q) return true;
    return i.symbol.toLowerCase().includes(q) || (i.name || "").toLowerCase().includes(q);
  });
}

function applyFilter() { renderGrid(); }

// ── Render grid ──────────────────────────────────────────
function renderGrid() {
  if (!grid) return;
  const items = filtered();
  updateDashboard(items);
  if (!items.length) {
    grid.innerHTML = `<div class="empty">
      <div class="empty-icon"><svg width="48" height="48" viewBox="0 0 48 48" fill="none"><rect x="8" y="14" width="32" height="26" rx="3" stroke="currentColor" stroke-width="1.5"/><path d="M8 22h32" stroke="currentColor" stroke-width="1.5"/><circle cx="16" cy="30" r="2" fill="currentColor"/><circle cx="24" cy="30" r="2" fill="currentColor"/><circle cx="32" cy="30" r="2" fill="currentColor"/></svg></div>
      <p class="empty-text">${Object.keys(symbolMap).length ? "没有匹配的标的" : "尚未添加任何标的"}</p>
      <p class="empty-hint">${Object.keys(symbolMap).length ? "试试其他搜索词" : "在侧栏输入代码即可开始监控"}</p>
    </div>`;
    return;
  }

  grid.innerHTML = items.map(i => {
    const k = N(i.symbol);
    const p = priceMap[k];
    if (!p) {
      return `<div class="card is-loading" role="button" tabindex="0" aria-label="查看 ${i.symbol} 行情详情" onkeydown="if(event.target===this && (event.key==='Enter'||event.key===' ')){event.preventDefault();openModal('${i.symbol}')}" onclick="openModal('${i.symbol}')">
        <div class="card-loading">加载中…</div>
        <div class="card-header"><span class="card-symbol">${i.symbol}</span><span class="card-market">${label(i.symbol)}</span></div>
        <div class="card-name">${escapeHTML(i.name || "")}</div>
      </div>`;
    }

    const up = p.change >= 0;
    const cls = p.change > 0 ? "up" : p.change < 0 ? "down" : "";
    const sign = p.change > 0 ? "+" : "";

    return `<div class="card" role="button" tabindex="0" aria-label="查看 ${i.symbol} 行情详情" onkeydown="if(event.target===this && (event.key==='Enter'||event.key===' ')){event.preventDefault();openModal('${i.symbol}')}" onclick="openModal('${i.symbol}')">
      <div class="card-header">
        <span class="card-symbol">${i.symbol}</span>
        <span class="card-market">${label(i.symbol)}</span>
      </div>
      <div class="card-name">${escapeHTML(i.name || p.name || "")}</div>
      <div class="card-price-row">
        <span class="card-price">${fmt(p.price)}</span>
        <span class="card-change ${cls}">${sign}${fmt(p.change)} (${p.changePct == null ? '--' : sign + p.changePct.toFixed(2)}%)</span>
      </div>
      ${p.market === "futures" ? `<div class="futures-meta">${escapeHTML(p.unit)} · ${escapeHTML(p.exchange || '')} · ${escapeHTML(p.contractMonth || "连续合约 · 自动换月")}<br>${p.source === 'sina' ? '新浪财经 · 涨跌相对昨结算' : 'Yahoo Finance'} · 报价可能延迟</div>` : ''}
      <div class="card-sparkline"><canvas id="spk-${k}" width="280" height="36" aria-label="本页价格采样走势"></canvas>${(priceHistory[k]?.length || 0) < 2 ? '<span class="sampling-label">等待价格采样</span>' : ''}</div>
      <div class="card-footer"><span class="card-time" title="${escapeHTML(p.quoteTime ? '数据源最近成交时间：' + new Date(p.quoteTime).toLocaleString('zh-CN', {timeZone:'Asia/Shanghai'}) + '（北京时间）' : '报价获取时间')}">${p.market === 'futures' ? '报价 ' : ''}${ago(p.updated)}</span><span>走势 ↗</span></div>
    </div>`;
  }).join("");

  requestAnimationFrame(() => { items.forEach(i => { const p = priceMap[N(i.symbol)]; if (p) spark("spk-" + N(i.symbol), p); }); });
}

function updateDashboard(items) {
  const all = Object.values(symbolMap);
  const quotes = all.map(i => priceMap[N(i.symbol)]).filter(Boolean);
  const up = quotes.filter(p => p.change > 0).length;
  const down = quotes.filter(p => p.change < 0).length;
  $("#summaryTotal").innerHTML = `${all.length}<small>项</small>`;
  $("#summaryUp").textContent = quotes.length ? up : "--";
  $("#summaryDown").textContent = quotes.length ? down : "--";
  $("#summaryOther").textContent = `${quotes.length - up - down} / ${all.length - quotes.length}`;
  const markets = [
    {name:"A股", key:"a-stock", color:"#254a70"},
    {name:"港股 / 指数", key:"hk", color:"#6e8cac"},
    {name:"美股", key:"us-stock", color:"#9caec4"},
    {name:"贵金属", key:"metal", color:"#b79658"},
    {name:"期货合约", key:"futures", color:"#997f60"},
    {name:"加密货币", key:"crypto", color:"#57988f"},
    {name:"其他", key:"unknown", color:"#bec8d5"},
  ].map(m => ({...m, count:all.filter(i => m.key === "hk" ? ["hk-stock","index"].includes(classify(i.symbol)) : classify(i.symbol) === m.key).length})).filter(m => m.count);
  $("#distributionBar").innerHTML = markets.map(m => `<span style="width:${m.count / all.length * 100}%;background:${m.color}"></span>`).join("");
  $("#marketDistribution").innerHTML = markets.length ? markets.map(m => `<div class="distribution-item"><span><i style="background:${m.color}"></i>${m.name}</span><strong>${m.count}<small> 项</small></strong></div>`).join("") : '<p class="distribution-caption">添加自选后查看市场分布</p>';
}

// 独立于自选与搜索条件的全球市场行情。
let globalMarketItems = [];
let globalMarketCategory = "index";
let globalMarketsLoading = false;
let globalMarketsTimer = null;
function startGlobalMarkets() {
  if (!$("#globalMarkets")) return;
  $$(".global-tab").forEach(button => button.addEventListener("click", () => {
    globalMarketCategory = button.dataset.category;
    $$(".global-tab").forEach(b => {
      b.classList.toggle("is-active", b === button);
      b.setAttribute("aria-pressed", String(b === button));
    });
    renderGlobalMarkets();
  }));
  loadGlobalMarkets();
  if (globalMarketsTimer) clearInterval(globalMarketsTimer);
  globalMarketsTimer = setInterval(loadGlobalMarkets, 60000);
}
async function loadGlobalMarkets() {
  if (globalMarketsLoading) return;
  globalMarketsLoading = true;
  try {
    const response = await api("/api/global-markets");
    globalMarketItems = response.items;
    renderGlobalMarkets();
  } catch(error) {
    $("#globalMarketStatus").textContent = "行情源连接失败";
    if (!globalMarketItems.length) $("#quoteTable").innerHTML = '<tr><td colspan="4" class="table-empty">全球行情暂不可用，请点击顶部“同步行情”重试</td></tr>';
    else {
      globalMarketItems = globalMarketItems.map(i => ({...i, stale:!i.unavailable}));
      renderGlobalMarkets();
      $("#globalMarketStatus").textContent = "连接失败 · 保留上次报价";
    }
  } finally { globalMarketsLoading = false; }
}
function renderGlobalMarkets() {
  const items = globalMarketItems.filter(i => i.category === globalMarketCategory);
  const available = items.filter(i => !i.unavailable).length;
  $("#globalMarketStatus").textContent = `${available}/${items.length} 项 · 60秒刷新`;
  $("#quoteTable").innerHTML = items.length ? items.map(i => {
    const cls = i.changePct > 0 ? "up" : i.changePct < 0 ? "down" : "";
    const pct = i.changePct == null ? "--" : `${i.changePct > 0 ? "+" : ""}${i.changePct.toFixed(2)}%`;
    const price = i.price == null ? "--" : i.price.toLocaleString("zh-CN", {minimumFractionDigits:2,maximumFractionDigits:i.instrumentType === "index" ? 2 : 4});
    const quoted = i.quoteTime ? new Date(i.quoteTime).toLocaleString("zh-CN", {month:"2-digit",day:"2-digit",hour:"2-digit",minute:"2-digit",hour12:false,timeZone:"Asia/Shanghai"}) : "--";
    const status = i.unavailable ? "暂不可用" : i.stale ? "上次成功报价" : "最近成交";
    return `<tr><td><strong>${escapeHTML(i.name)}</strong><small>${escapeHTML(i.region)} · ${escapeHTML(i.symbol)}${i.instrumentType === "futures" ? " · 期货" : i.category === "commodity" ? " · 指数" : ""}</small></td><td>${price}<small>${escapeHTML(i.unit)}</small></td><td class="${cls}">${pct}</td><td class="quote-time">${quoted}<small class="${i.stale || i.unavailable ? "quote-warning" : ""}">${status}</small></td></tr>`;
  }).join("") : '<tr><td colspan="4" class="table-empty">正在获取全球市场行情…</td></tr>';
}

// ── Sparkline ────────────────────────────────────────────
function spark(id, p) {
  const c = document.getElementById(id);
  if (!c) return;
  const ctx = c.getContext("2d");
  const w = c.width, h = c.height;
  ctx.clearRect(0, 0, w, h);

  const up = p.change >= 0;
  const col = up ? "rgba(206,65,75,0.75)" : "rgba(22,130,113,0.75)";
  const fill = up ? "rgba(206,65,75,0.06)" : "rgba(22,130,113,0.06)";

  const d = priceHistory[N(p.symbol)] || [];
  if (d.length < 2) return;
  const n = d.length;

  const mn = Math.min(...d)*0.999, mx = Math.max(...d)*1.001, rng = mx-mn||1;
  const pd = 2, pw = w-pd*2, ph = h-pd*2;
  const xs = d.map((_,i) => pd+(i/(n-1))*pw);
  const ys = d.map(v => pd+ph-((v-mn)/rng)*ph);

  ctx.beginPath(); ctx.moveTo(xs[0], h);
  for (let i = 0; i < xs.length; i++) ctx.lineTo(xs[i], ys[i]);
  ctx.lineTo(xs[xs.length-1], h); ctx.closePath();
  ctx.fillStyle = fill; ctx.fill();

  ctx.beginPath();
  for (let i = 0; i < xs.length; i++) { if (!i) ctx.moveTo(xs[i], ys[i]); else ctx.lineTo(xs[i], ys[i]); }
  ctx.strokeStyle = col; ctx.lineWidth = 1.5; ctx.stroke();
}

// ── Modal ────────────────────────────────────────────────
async function openModal(sym) {
  modalSymbol = sym;
  const k = N(sym);
  const e = symbolMap[k];
  const p = priceMap[k];
  modTitle.textContent = `${e?.name || p?.name || sym} (${sym})`;

  $$(".sym-item").forEach(el => el.classList.remove("is-active"));
  const sel = document.querySelector(`.sym-item[data-sym="${sym}"]`);
  if (sel) sel.classList.add("is-active");

  modStats.textContent = "行情加载中…";
  if (p) {
    modStats.innerHTML = `
      <div class="stat-item"><span class="stat-value ${p.change>=0?"up":"down"}">${fmt(p.price)}</span><span class="stat-label">最新价</span></div>
      <div class="stat-item"><span class="stat-value">${fmt(p.high)}</span><span class="stat-label">最高</span></div>
      <div class="stat-item"><span class="stat-value">${fmt(p.low)}</span><span class="stat-label">最低</span></div>
      <div class="stat-item"><span class="stat-value ${p.change>=0?"up":"down"}">${p.change>=0?"+":""}${p.changePct}%</span><span class="stat-label">涨跌幅</span></div>`;
  }

  backdrop.classList.add("is-open");

  $$(".intv").forEach(b => b.classList.remove("is-active"));
  const initialInterval = sym.toUpperCase() === "HSTECH" ? "1d" : "1h";
  $$(".intv").forEach(b => {
    b.disabled = sym.toUpperCase() === "HSTECH" && b.dataset.interval !== "1d";
    b.classList.toggle("is-active", b.dataset.interval === initialInterval);
  });
  await loadKline(sym, initialInterval);
}

function closeModal() {
  backdrop.classList.remove("is-open");
  modalSymbol = null;
  if (klineController) klineController.abort();
  kState = null;
  if (chartInstance) { chartInstance.destroy(); chartInstance = null; }
  $$(".sym-item").forEach(el => el.classList.remove("is-active"));
}

function updateStats(sym) {
  const p = priceMap[N(sym)];
  if (!p) return;
  modStats.innerHTML = `
    <div class="stat-item"><span class="stat-value ${p.change>=0?"up":"down"}">${fmt(p.price)}</span><span class="stat-label">最新价</span></div>
    <div class="stat-item"><span class="stat-value">${fmt(p.high)}</span><span class="stat-label">最高</span></div>
    <div class="stat-item"><span class="stat-value">${fmt(p.low)}</span><span class="stat-label">最低</span></div>
    <div class="stat-item"><span class="stat-value ${p.change>=0?"up":"down"}">${p.change>=0?"+":""}${p.changePct}%</span><span class="stat-label">涨跌幅</span></div>`;
}

// ── Kline (Pure Canvas — 雪球风格) ──────────────────────
let kState = null; // { ohlc, ma5, ma10, ma20, minP, maxP, volMax, canvas, ctx, W, H, mainH, volH, margin, barW, cx, cy }

let klineController = null;
async function loadKline(sym, intv) {
  if (klineController) klineController.abort();
  const controller = klineController = new AbortController();
  kState = null;
  const canvas = document.getElementById("klineChart");
  canvas.getContext("2d").clearRect(0, 0, canvas.width, canvas.height);
  chartLoading.textContent = "加载中…";
  chartLoading.classList.remove("is-hidden");
  crossEl.textContent = "";
  $("#klineSource").textContent = "";
  try {
    const r = await fetch(API.kline(sym) + `?interval=${intv}&limit=150`, { signal: controller.signal });
    const d = await r.json();
    if (!r.ok) throw Error(d.detail || "K线数据暂不可用");
    if (controller !== klineController || modalSymbol !== sym) return;
    if (!Array.isArray(d.data) || d.data.length < 2) throw Error("暂无K线数据");
    const sourceNames = {yahoo:"Yahoo Finance",sina:"新浪财经",tencent:"腾讯财经",binance:"Binance"};
    $("#klineSource").textContent = [sourceNames[d.source] || d.source, d.providerSymbol, d.notice, d.aggregation].filter(Boolean).join(" · ");
    $$(".intv").forEach(b => { b.disabled = !d.supportedIntervals.includes(b.dataset.interval); });
    drawKline(d.data, sym);
    chartLoading.classList.add("is-hidden");
  } catch(e) {
    if (e.name !== "AbortError" && controller === klineController) chartLoading.textContent = e.message || "K线数据暂不可用";
  }
}

function drawKline(kdata, sym) {
  const canvas = document.getElementById("klineChart");
  const parent = canvas.parentElement;
  const dpr = window.devicePixelRatio || 1;

  // 尺寸
  const rect = parent.getBoundingClientRect();
  const W = rect.width;
  const H = rect.height;
  canvas.width = W * dpr;
  canvas.height = H * dpr;
  canvas.style.width = W + "px";
  canvas.style.height = H + "px";

  const ctx = canvas.getContext("2d");
  ctx.scale(dpr, dpr);

  if (!kdata || kdata.length < 2) {
    chartLoading.textContent = "暂无K线数据";
    chartLoading.classList.remove("is-hidden");
    return;
  }

  // ── OHLC 数据 ──
  const ohlc = kdata.map(k => ({ t: k.time, o: k.open, h: k.high, l: k.low, c: k.close, v: k.volume }));

  // ── 计算 MA ──
  const ma5 = MA(ohlc, 5);
  const ma10 = MA(ohlc, 10);
  const ma20 = MA(ohlc, 20);

  // ── 范围 ──
  let minP = Infinity, maxP = -Infinity, volMax = 0;
  ohlc.forEach(k => {
    if (k.h > maxP) maxP = k.h;
    if (k.l < minP) minP = k.l;
    if (k.v > volMax) volMax = k.v;
    // MA 也参与范围
  });
  [ma5, ma10, ma20].forEach(arr => {
    arr.forEach(v => {
      if (v !== null && v.y != null) {
        if (v.y > maxP) maxP = v.y;
        if (v.y < minP) minP = v.y;
      }
    });
  });

  const pad = Math.max((maxP - minP) * 0.05, minP * 0.005);
  const priceMin = minP - pad;
  const priceMax = maxP + pad;
  volMax = Math.max(1, volMax * 1.1);

  // ── 布局 ──
  const margin = { top: 10, right: 56, bottom: 22, left: 4 };
  const mainH = H * 0.72;
  const volH = Math.max(1, H - margin.top - mainH - margin.bottom);
  const plotL = margin.left;
  const plotR = W - margin.right;
  const plotW = plotR - plotL;

  // ── 存储状态 ──
  kState = { ohlc, ma5, ma10, ma20, priceMin, priceMax, volMax, canvas, ctx, W, H, mainH, volH, margin: { ...margin, plotL, plotR, plotW }, barW: plotW / ohlc.length, sym };

  // ── 绘制 ──
  renderChart(ctx, kState, null);

  // 绑定鼠标交互
  setTimeout(setupKlineInteraction, 50);
}

function renderChart(ctx, s, hoverIdx) {
  const { ohlc, ma5, ma10, ma20, priceMin, priceMax, volMax, W, H, mainH, volH, margin, barW, sym } = s;
  const { top, plotL, plotR, plotW, bottom } = margin;
  const dpr = window.devicePixelRatio || 1;

  // 清空
  ctx.clearRect(0, 0, W, H);

  // ── 背景 ──
  ctx.fillStyle = "#ffffff";
  ctx.fillRect(0, 0, W, H);

  // ── 坐标映射 ──
  const toY = (v) => top + mainH - ((v - priceMin) / (priceMax - priceMin)) * mainH;
  const toVolY = (v) => top + mainH + volH - (v / volMax) * volH;

  // ── 水平网格线 ──
  ctx.strokeStyle = "rgba(32,60,90,0.10)";
  ctx.lineWidth = 0.5;
  const gridCount = 5;
  for (let i = 0; i <= gridCount; i++) {
    const y = top + (mainH / gridCount) * i;
    ctx.beginPath(); ctx.moveTo(plotL, y); ctx.lineTo(plotR, y); ctx.stroke();
  }

  // ── 价格标签（Y轴右侧） ──
  ctx.font = "10px 'SF Mono',Menlo,monospace";
  ctx.textAlign = "right";
  ctx.fillStyle = "#64748b";
  for (let i = 0; i <= gridCount; i++) {
    const y = top + (mainH / gridCount) * i;
    const val = priceMax - ((priceMax - priceMin) / gridCount) * i;
    ctx.fillText(val.toFixed(2), plotR + 6, y + 4);
  }

  // ── 成交量分割线 ──
  const volTop = top + mainH;
  ctx.strokeStyle = "rgba(32,60,90,0.13)";
  ctx.beginPath(); ctx.moveTo(plotL, volTop); ctx.lineTo(plotR, volTop); ctx.stroke();

  // ── 时间标签（X轴） ──
  ctx.font = "9px -apple-system,'PingFang SC',sans-serif";
  ctx.textAlign = "center";
  ctx.fillStyle = "#64748b";
  const labelCount = Math.min(8, ohlc.length);
  const step = Math.max(1, Math.floor(ohlc.length / labelCount));
  for (let i = 0; i < ohlc.length; i += step) {
    const x = plotL + (i / ohlc.length) * plotW;
    const d = new Date(ohlc[i].t * 1000);
    const label = d.toLocaleDateString("zh-CN", { month: "short", day: "numeric" });
    ctx.fillText(label, x, top + mainH + volH + 16);
  }

  // ── K线 ──
  const gap = Math.max(0, Math.min(2, barW * 0.15));
  const candleW = Math.max(2, barW - gap);

  for (let i = 0; i < ohlc.length; i++) {
    const k = ohlc[i];
    const x = plotL + (i / ohlc.length) * plotW + (plotW / ohlc.length) / 2;
    const isUp = k.c >= k.o;
    const color = isUp ? "#E8474C" : "#099A51";
    const yHi = toY(k.h);
    const yLo = toY(k.l);
    const yO = toY(k.o);
    const yC = toY(k.c);

    ctx.save();

    // 影线
    ctx.beginPath();
    ctx.moveTo(x, yHi);
    ctx.lineTo(x, yLo);
    ctx.strokeStyle = color;
    ctx.lineWidth = 0.8;
    ctx.stroke();

    // 实体
    const halfW = Math.max(1, candleW / 2);
    if (isUp) {
      ctx.fillStyle = color;
      ctx.fillRect(x - halfW, yC, candleW, Math.max(1, yO - yC));
    } else {
      ctx.fillStyle = color;
      ctx.fillRect(x - halfW, yO, candleW, Math.max(1, yC - yO));
    }

    ctx.restore();
  }

  // ── 均线 ──
  function drawMA(arr, color) {
    ctx.save();
    ctx.strokeStyle = color;
    ctx.lineWidth = 1.2;
    ctx.beginPath();
    let started = false;
    for (let i = 0; i < arr.length; i++) {
      if (arr[i] === null || arr[i].y == null) continue;
      const x = plotL + (i / ohlc.length) * plotW + (plotW / ohlc.length) / 2;
      const y = toY(arr[i].y);
      if (!started) { ctx.moveTo(x, y); started = true; }
      else ctx.lineTo(x, y);
    }
    ctx.stroke();
    ctx.restore();
  }

  drawMA(ma5, "#b27720");
  drawMA(ma10, "#2867ae");
  drawMA(ma20, "#E8474C");

  // ── 均线图例（右上角） ──
  ctx.font = "10px -apple-system,'PingFang SC',sans-serif";
  ctx.textAlign = "left";
  const legends = [
    { label: "MA5", color: "#b27720", val: ma5[ma5.length-1] },
    { label: "MA10", color: "#2867ae", val: ma10[ma10.length-1] },
    { label: "MA20", color: "#E8474C", val: ma20[ma20.length-1] },
  ];
  let lx = plotL + 8;
  const ly = top + 6;
  legends.forEach(l => {
    if (l.val && l.val.y != null) {
      ctx.fillStyle = l.color;
      ctx.fillRect(lx, ly + 3, 8, 2);
      ctx.fillText(`${l.label} ${l.val.y.toFixed(2)}`, lx + 11, ly + 7);
      lx += ctx.measureText(`${l.label} ${l.val.y.toFixed(2)}`).width + 20;
    }
  });

  // ── 成交量 ──
  for (let i = 0; i < ohlc.length; i++) {
    const k = ohlc[i];
    const x = plotL + (i / ohlc.length) * plotW + (plotW / ohlc.length) / 2;
    const isUp = k.c >= k.o;
    const color = isUp ? "#E8474C" : "#099A51";
    const y = toVolY(k.v);
    const halfW = Math.max(1, candleW / 2);
    ctx.fillStyle = color + "66";
    ctx.fillRect(x - halfW, y, candleW, top + mainH + volH - y);
  }

  // ── 十字光标 ──
  if (hoverIdx !== null && hoverIdx >= 0 && hoverIdx < ohlc.length) {
    drawCrosshair(ctx, s, hoverIdx);
  }
}

function drawCrosshair(ctx, s, idx) {
  const { ohlc, priceMin, priceMax, W, H, mainH, volH, margin } = s;
  const { top, plotL, plotR, plotW } = margin;
  const k = ohlc[idx];
  if (!k) return;

  const x = plotL + (idx / ohlc.length) * plotW + (plotW / ohlc.length) / 2;
  const toY = (v) => top + mainH - ((v - priceMin) / (priceMax - priceMin)) * mainH;
  const yPrice = toY(k.c);

  ctx.save();

  // 垂直虚线
  ctx.beginPath();
  ctx.setLineDash([3, 4]);
  ctx.moveTo(x, top);
  ctx.lineTo(x, top + mainH + volH);
  ctx.strokeStyle = "rgba(32,60,90,0.35)";
  ctx.lineWidth = 0.5;
  ctx.stroke();

  // 水平虚线（收盘价）
  ctx.beginPath();
  ctx.moveTo(plotL, yPrice);
  ctx.lineTo(plotR, yPrice);
  ctx.strokeStyle = "rgba(32,60,90,0.35)";
  ctx.lineWidth = 0.5;
  ctx.stroke();

  ctx.setLineDash([]);

  // 右侧价格标签
  const priceText = k.c.toFixed(2);
  ctx.font = "10px 'SF Mono',Menlo,monospace";
  const tw = ctx.measureText(priceText).width;
  ctx.fillStyle = "rgba(16,27,44,0.96)";
  ctx.fillRect(plotR - tw - 10, yPrice - 8, tw + 12, 18);
  ctx.fillStyle = "#f5f5f7";
  ctx.textAlign = "left";
  ctx.fillText(priceText, plotR - tw - 5, yPrice + 5);

  // 顶部时间标签
  const d = new Date(k.t * 1000);
  const timeStr = d.toLocaleString("zh-CN", { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
  ctx.font = "10px -apple-system,'PingFang SC',sans-serif";
  const tw2 = ctx.measureText(timeStr).width;
  ctx.fillStyle = "rgba(16,27,44,0.96)";
  ctx.fillRect(x - tw2 / 2 - 6, top, tw2 + 12, 20);
  ctx.fillStyle = "#f5f5f7";
  ctx.textAlign = "center";
  ctx.fillText(timeStr, x, top + 14);

  ctx.restore();

  // 底部详情
  const isUp = k.c >= k.o;
  const arrow = isUp ? "↑" : "↓";
  const chg = k.c - k.o;
  const chgPct = k.o ? (chg / k.o * 100) : 0;
  crossEl.textContent =
    `${d.toLocaleString("zh-CN")}  ${arrow}  开:${k.o.toFixed(2)}  高:${k.h.toFixed(2)}  低:${k.l.toFixed(2)}  收:${k.c.toFixed(2)}  涨跌:${chg >= 0 ? "+" : ""}${chg.toFixed(2)} (${chg >= 0 ? "+" : ""}${chgPct.toFixed(2)}%)  量:${k.v.toFixed(0)}`;
}

// ── 鼠标交互 ──
let klineMouseMoveHandler = null;
let klineMouseLeaveHandler = null;

function setupKlineInteraction() {
  const canvas = document.getElementById("klineChart");

  if (klineMouseMoveHandler) {
    canvas.removeEventListener("mousemove", klineMouseMoveHandler);
    canvas.removeEventListener("mouseleave", klineMouseLeaveHandler);
  }

  klineMouseMoveHandler = (e) => {
    if (!kState || !kState.ohlc.length) return;
    const rect = canvas.getBoundingClientRect();
    const mx = e.clientX - rect.left;
    const { plotL, plotW } = kState.margin;
    const ratio = (mx - plotL) / plotW;
    const idx = Math.round(ratio * (kState.ohlc.length - 1));
    const clamped = Math.max(0, Math.min(kState.ohlc.length - 1, idx));
    if (mx >= plotL && mx <= plotL + plotW) {
      renderChart(kState.ctx, kState, clamped);
    }
  };

  klineMouseLeaveHandler = () => {
    if (!kState) return;
    crossEl.textContent = "";
    renderChart(kState.ctx, kState, null);
  };

  canvas.addEventListener("mousemove", klineMouseMoveHandler);
  canvas.addEventListener("mouseleave", klineMouseLeaveHandler);
}

// 覆盖原来的 renderKline 入口
function renderKline(kdata, sym) {
  drawKline(kdata, sym);
  // 延迟一帧绑定交互（等 canvas 渲染完成）
  setTimeout(setupKlineInteraction, 50);
}

// 窗口 resize
window.addEventListener("resize", () => {
  if (kState && kState.ohlc.length) {
    const data = kState.ohlc.map(k => ({time:k.t,open:k.o,high:k.h,low:k.l,close:k.c,volume:k.v}));
    drawKline(data, kState.sym);
  }
});

// ── Helpers ──────────────────────────────────────────────
function escapeHTML(value) {
  return String(value).replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
}

function N(s) { return (s||"").toUpperCase().replace(/_/g,""); }

function isFuture(sym) {
  const s = String(sym).toUpperCase();
  if (isDomesticFuture(s)) return true;
  const roots = {BZ:'NYM', CL:'NYM', NG:'NYM', GC:'CMX', SI:'CMX', HG:'CMX', ZC:'CBT', ZW:'CBT', ZS:'CBT'};
  const match = s.match(/^([A-Z]{2})(?:=F|([FGHJKMNQUVXZ])\d{2}\.([A-Z]{3}))$/);
  return !!match && !!roots[match[1]] && (!match[3] || roots[match[1]] === match[3]);
}

function isDomesticFuture(sym) {
  const match = String(sym).toUpperCase().match(/^([A-Z]{1,2})(0|\d{4})$/);
  const roots = 'RB HC AU AG CU AL ZN NI SN RU BU FU SC LU NR EC I J JM M Y P C A EG PP TA MA SA FG SR CF AP UR LC SI PS IF IH IC IM T TF TS TL'.split(' ');
  return !!match && roots.includes(match[1]) && (match[2] === '0' || (+match[2].slice(-2) >= 1 && +match[2].slice(-2) <= 12));
}

function classify(sym) {
  const s = sym.toUpperCase();
  if (isFuture(s)) return "futures";
  if (!["XAUUSDT","XAGUSDT"].includes(s) && (s === "ASTER" || /^[A-Z0-9]{2,16}USDT$/.test(s))) return "crypto";
  if (s.startsWith("HF_") || ["XAUUSD","XAGUSD","XAUUSDT","XAGUSDT","GOLD"].includes(s)) return "metal";
  if (sym.match(/^\d{6}$/)) return "a-stock";
  if (["HSTECH","HSI","HSCEI"].includes(s)) return "index";
  if (sym.match(/^\d{5}$/)) return "hk-stock";
  if (sym.match(/^[A-Z]{1,4}$/)) return "us-stock";
  return "unknown";
}

function label(sym) {
  const m = classify(sym);
  return { crypto:"加密货币", futures:"期货", metal:"贵金属", "a-stock":"A股", "us-stock":"美股", "hk-stock":"港股", index:"指数", unknown:"其他" }[m]||"其他";
}

function fmt(v) {
  if (v==null || isNaN(v)) return "--";
  if (Math.abs(v)>=1000) return v.toLocaleString("zh-CN",{minimumFractionDigits:2,maximumFractionDigits:2});
  if (Math.abs(v)>=1) return v.toFixed(4);
  return v.toFixed(6);
}

function ago(s) {
  if (!s) return "";
  const sec = Math.floor((Date.now()-new Date(s).getTime())/1000);
  if (sec<10) return "刚刚";
  if (sec<60) return `${sec}秒前`;
  return `${Math.floor(sec/60)}分钟前`;
}

function MA(data, n) {
  return data.map((_,i) => {
    if (i<n-1) return null;
    let s=0; for(let j=i-n+1;j<=i;j++) s+=data[j].c;
    return {x:data[i].t, y:s/n};
  });
}

let symbolSearchTimer = null;
let symbolSearchController = null;
function cancelSymbolSearch() {
  clearTimeout(symbolSearchTimer);
  if (symbolSearchController) symbolSearchController.abort();
  symbolSearchController = null;
}
function hideSymbolSuggestions() {
  $("#symbolSuggestions").hidden = true;
  symInp.setAttribute("aria-expanded", "false");
}
async function searchSymbolSuggestions() {
  const query = symInp.value.trim();
  cancelSymbolSearch();
  if (!query) return hideSymbolSuggestions();
  const controller = symbolSearchController = new AbortController();
  const box = $("#symbolSuggestions");
  box.hidden = false;
  symInp.setAttribute("aria-expanded", "true");
  box.textContent = "搜索中…";
  try {
    const response = await api("/api/search?q=" + encodeURIComponent(query), {signal:controller.signal});
    if (controller !== symbolSearchController || symInp.value.trim() !== query) return;
    box.replaceChildren();
    if (!response.items.length) { box.textContent = "未找到匹配标的，可尝试完整名称或代码"; return; }
    response.items.forEach(item => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "symbol-suggestion";
      button.innerHTML = `<strong>${escapeHTML(item.name)}</strong><small>${escapeHTML(item.symbol)} · ${label(item.symbol)}</small>`;
      button.addEventListener("click", () => {
        cancelSymbolSearch();
        symInp.value = item.symbol;
        if (!nameInp.value.trim() || nameInp.dataset.autofilled) {
          nameInp.value = item.name;
          nameInp.dataset.autofilled = "1";
        }
        hideSymbolSuggestions();
        $("#btnAdd").focus();
      });
      button.addEventListener("keydown", e => {
        if (e.key === "ArrowDown") { e.preventDefault(); button.nextElementSibling?.focus(); }
        if (e.key === "ArrowUp") { e.preventDefault(); (button.previousElementSibling || symInp).focus(); }
        if (e.key === "Escape") { hideSymbolSuggestions(); symInp.focus(); }
      });
      box.appendChild(button);
    });
    if (response.partial) {
      const note = document.createElement("p");
      note.className = "search-note";
      note.textContent = "网络搜索暂不可用，仅显示本地匹配";
      box.appendChild(note);
    }
  } catch (error) {
    if (error.name !== "AbortError" && controller === symbolSearchController) box.textContent = error.message;
  }
}

function toast(msg) {
  let el = document.getElementById("toast");
  if (!el) {
    el = document.createElement("div"); el.id = "toast"; el.className = "toast"; document.body.appendChild(el);
  }
  el.textContent = msg; el.style.display = "block";
  clearTimeout(el._t); el._t = setTimeout(() => { el.style.display = "none"; }, 2800);
}

// ── Boot ─────────────────────────────────────────────────
document.addEventListener("DOMContentLoaded", init);
