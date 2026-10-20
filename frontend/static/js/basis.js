/* Signed quarterly spread monitoring. No invented points or interpolation. */
let basisHistory=null, basisRange=365, basisLoading=false, basisHistoryLoading=false, basisHistoryRetry=null;
const basisPercent=v=>v==null?'--':`${Number(v).toFixed(2)}%`;
function basisLocalTime(s){return s?new Date(s).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false}):'--';}

async function refreshBasis(){
  if(basisLoading)return;basisLoading=true;
  try{
    const data=await api('/api/basis');
    $('#basisCards').innerHTML=data.items.map(i=>`<article class="basis-card"><div class="basis-card-heading"><h2>${i.symbol} <small>${i.symbol==='IC'?'中证500':'中证1000'}</small></h2><span>${i.available?(i.stale?'最近报价 · 已过时':'两腿报价同步'):'数据暂不可用'}</span></div><strong class="basis-rate ${i.symbol.toLowerCase()}">${basisPercent(i.rate)}</strong><p>${escapeHTML(i.frontSymbol)} → ${escapeHTML(i.farSymbol)}</p><dl><div><dt>当季价格</dt><dd>${fmt(i.frontPrice)}</dd></div><div><dt>下季价格</dt><dd>${fmt(i.farPrice)}</dd></div></dl><p class="basis-time">${i.available?`当季 ${basisLocalTime(i.frontQuoteTime)}<br>下季 ${basisLocalTime(i.farQuoteTime)}`:escapeHTML(i.error||'等待行情源')}</p>${i.stale?'<p class="basis-warning">休市或报价延迟超过2分钟，暂停阈值告警。</p>':''}</article>`).join('');
    $('#basisStatus').textContent='新浪财经 · 约30秒刷新 · 时间为北京时间'+(data.items.some(i=>i.cached)?' · 行情源暂不可用，显示最近有效快照，不触发告警':'');
  }catch(e){$('#basisStatus').textContent=e.message;}
  finally{basisLoading=false;}
}

async function loadBasisHistory(){
  if(basisHistoryLoading)return;basisHistoryLoading=true;
  clearTimeout(basisHistoryRetry);
  $('#basisHistoryStatus').textContent='正在获取真实季月合约历史日线…';
  try{
    basisHistory=await api('/api/basis/history');
    renderBasisChart();
    if(basisHistory.retrySeconds<3600)basisHistoryRetry=setTimeout(loadBasisHistory,15000);
  }catch(e){$('#basisHistoryStatus').textContent=e.message+' · 15秒后自动重试';if(!basisHistory)$('#basisChart').textContent='历史数据暂不可用，正在自动重试。';basisHistoryRetry=setTimeout(loadBasisHistory,15000);}
  finally{basisHistoryLoading=false;}
}

function renderBasisChart(){
  if(!basisHistory)return;
  const end=new Date(basisHistory.endDate+'T00:00:00+08:00');
  const cutoff=new Date(end.getTime()-(basisRange-1)*86400000);
  const data=basisHistory.series.map(s=>({...s,points:s.points.filter(p=>new Date(p.date+'T00:00:00+08:00')>=cutoff)}));
  const valid=data.flatMap(s=>s.points.filter(p=>p.rate!=null));
  const count=data.map(s=>`${s.symbol} ${s.points.filter(p=>p.rate!=null).length}个有效日`).join(' · ');
  $('#basisHistoryStatus').textContent=`${count} · 截至 ${basisHistory.snapshotEndDate||basisHistory.endDate}（不含当日未收盘数据）${basisHistory.cached?' · 最近有效历史快照，正在重试':''}${basisHistory.retrySeconds<3600?' · 15秒后自动重试':''}`;
  $('#basisExtremes').innerHTML=data.map(s=>{
    const rows=s.points.filter(p=>p.rate!=null);if(!rows.length)return `<p>${s.symbol} 暂无有效历史</p>`;
    const deepest=rows.reduce((a,b)=>a.rate<b.rate?a:b);
    return `<p><strong>${s.symbol}</strong> 所选区间最深 <b>${basisPercent(deepest.rate)}</b> · ${escapeHTML(deepest.date)} · ${escapeHTML(deepest.frontSymbol)} / ${escapeHTML(deepest.farSymbol)}</p>`;
  }).join('');
  $('#basisCoverage').textContent=`${basisHistory.notice} ${data.map(s=>`${s.symbol}缺失${s.points.filter(p=>p.rate==null).length}日`).join('，')}。${basisHistory.failedContracts.length?'部分合约历史源不可用：'+basisHistory.failedContracts.join('、')+'。':''}`;
  if(!valid.length){$('#basisChart').textContent='当前区间暂无两腿同日有效价格。';return;}
  const W=1040,H=430,m={l:68,r:52,t:26,b:46},pw=W-m.l-m.r,ph=H-m.t-m.b;
  const dates=valid.map(p=>Date.parse(p.date));const lo=Math.min(...dates),hi=Math.max(...dates);
  const low=Math.floor(Math.min(...valid.map(p=>p.rate))-1),high=Math.ceil(Math.max(...valid.map(p=>p.rate))+1);
  const x=p=>m.l+(Date.parse(p.date)-lo)/(hi-lo||1)*pw;
  const y=p=>m.t+(high-p.rate)/(high-low||1)*ph;
  let svg=`<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="IC和IM当季至下季年化贴水历史曲线，纵轴数值越负，贴水越深">`;
  for(let j=0;j<=5;j++){const value=high-(high-low)*j/5,yy=m.t+ph*j/5;svg+=`<line x1="${m.l}" y1="${yy}" x2="${W-m.r}" y2="${yy}" stroke="#e0e7ee"/><text x="${m.l-12}" y="${yy+5}" text-anchor="end" fill="#7b8b9d" font-size="13">${value.toFixed(1)}%</text>`;}
  for(let j=0;j<=5;j++){const ts=lo+(hi-lo)*j/5,xx=m.l+pw*j/5;svg+=`<text x="${xx}" y="${H-14}" text-anchor="middle" fill="#7b8b9d" font-size="12">${new Date(ts).toISOString().slice(0,10)}</text>`;}
  data.forEach(s=>{
    const color=s.symbol==='IM'?'#506dff':'#91b12e';let segment=[],lastPair=null;
    const flush=()=>{if(segment.length>1)svg+=`<polyline points="${segment.join(' ')}" fill="none" stroke="${color}" stroke-width="2.4"/>`;segment=[];};
    s.points.forEach(p=>{
      const pair=p.frontSymbol+'/'+p.farSymbol;
      if(p.rate==null){flush();lastPair=null;return;}
      if(lastPair && lastPair!==pair){flush();svg+=`<line x1="${x(p)}" y1="${m.t}" x2="${x(p)}" y2="${H-m.b}" stroke="${color}" stroke-dasharray="3 6" opacity=".28"/>`;}
      segment.push(`${x(p)},${y(p)}`);lastPair=pair;
      svg+=`<circle cx="${x(p)}" cy="${y(p)}" r="3" fill="${color}" tabindex="0" data-root="${s.symbol}" data-date="${p.date}"><title>${s.symbol} ${p.date} ${basisPercent(p.rate)} ${pair}</title></circle>`;
    });flush();
  });
  $('#basisChart').innerHTML=svg+'</svg>';
  $('#basisChart').querySelectorAll('circle').forEach(circle=>{
    const show=()=>{const s=data.find(s=>s.symbol===circle.dataset.root),p=s.points.find(p=>p.date===circle.dataset.date);$('#basisPoint').textContent=`${s.symbol} · ${p.date} · ${basisPercent(p.rate)} · 当季 ${p.frontSymbol} ${fmt(p.frontPrice)} · 下季 ${p.farSymbol} ${fmt(p.farPrice)}`;};
    circle.onmouseenter=show;circle.onfocus=show;circle.onclick=show;
  });
}

document.addEventListener('DOMContentLoaded',()=>{
  refreshBasis();loadBasisHistory();setInterval(refreshBasis,30000);
  $('#basisRefresh').onclick=()=>{refreshBasis();loadBasisHistory();};
  document.querySelectorAll('[data-basis-range]').forEach(b=>b.onclick=()=>{basisRange=Number(b.dataset.basisRange);document.querySelectorAll('[data-basis-range]').forEach(t=>t.classList.toggle('is-active',t===b));renderBasisChart();});
});
