/* Alert controls, history and opt-in browser notifications. WeChat is delivered by the backend. */
let alertState = {rules:[],events:[],wechat:{enabled:false,configured:false}};
let alertStateLoaded = false;
let alertStateLoading = false;
let alertPollTimer = null;
let alertSeenIds = new Set();
// 兼容更名前保存的已读记录，避免升级后重复通知。
try { const saved=JSON.parse(localStorage.getItem('financial-asset-monitor-alert-seen') || localStorage.getItem('market-pulse-alert-seen') || '[]'); if(Array.isArray(saved)) alertSeenIds=new Set(saved); } catch(_) {}

function rememberAlert(id) {
  alertSeenIds.add(id);
  if(alertSeenIds.size>300) alertSeenIds=new Set([...alertSeenIds].slice(-200));
  try { localStorage.setItem('financial-asset-monitor-alert-seen',JSON.stringify([...alertSeenIds])); } catch(_) {}
}
function alertTime(value) {
  return new Date(value).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false});
}
function alertMessage(event) {
  if(event.kind==='basis') return `${event.symbol} 年化贴水 ${Number(event.rate).toFixed(2)}%，${event.frontSymbol} → ${event.farSymbol}，达到 ${event.operator==='lte'?'≤':'≥'} ${event.thresholdPct}%`;
  return `${event.name}（${event.symbol}）约${event.windowMinutes}分钟内${event.direction==='up'?'上涨':'下跌'} ${Math.abs(event.changePct).toFixed(2)}%，现价 ${fmt(event.price)}`;
}
function receiveAlertEvent(event,notify=true) {
  if(!event || !event.id) return;
  const index=alertState.events.findIndex(e=>e.id===event.id);
  if(index>=0) alertState.events[index]={...alertState.events[index],...event};
  else alertState.events.unshift(event);
  alertState.events=alertState.events.slice(0,50);
  const unseen=!alertSeenIds.has(event.id);
  rememberAlert(event.id);
  if(notify && unseen) {
    const message=alertMessage(event);
    const banner=$('#alertBanner');
    if (banner) { banner.hidden=false; banner.textContent='波动预警：'+message+'。详情见预警中心。'; }
    toast(message);
    if('Notification' in window && Notification.permission==='granted') {
      try { new Notification('金融资产监控看板 行情预警',{body:message,tag:event.id}); } catch(_) {}
    }
  }
  renderAlertCenter();
}
function updateAlertDelivery(data) {
  const event=alertState.events.find(e=>e.id===data.id);
  if(event) Object.assign(event,data);
  renderAlertCenter();
}
async function loadAlertState() {
  if(alertStateLoading) return;
  alertStateLoading=true;
  try {
    const next=await api('/api/alerts');
    const shouldNotify=alertStateLoaded;
    const events=next.events || [];
    alertState={...next,events:[]};
    // Oldest first ensures the latest alert remains the banner after reconnecting.
    [...events].reverse().forEach(event=>receiveAlertEvent(event,shouldNotify));
    alertStateLoaded=true;
    renderAlertCenter();
  } catch(error) { if ($('#alertRules')) $('#alertRules').textContent=error.message; }
  finally { alertStateLoading=false; }
}
function updateAlertSymbols() {
  const select=$('#alertSymbol');
  if (!select) return;
  const selected=select.value;
  select.innerHTML=Object.values(symbolMap).map(item=>`<option value="${item.symbol}">${escapeHTML(item.name || item.symbol)} · ${item.symbol}</option>`).join('');
  if(symbolMap[N(selected)]) select.value=selected;
  $('#saveAlertRule').disabled=!Object.keys(symbolMap).length;
}
function syncWechatFields() {
  if (!$('#wechatEnabled')) return;
  $('#wechatEnabled').checked=alertState.wechat.enabled;
  $('#wechatSendKey').value='';
  const last=alertState.wechat.lastTest;
  $('#wechatAlertStatus').textContent=last ? `最近测试：${alertTime(last.time)} · ${last.status==='sent'?'Server酱已受理，尚未确认微信送达；如未收到，请检查官网消息通道、绑定微信和发送记录。':last.error || '测试未成功'}` : alertState.wechat.configured ? (alertState.wechat.enabled?'密钥已保存，发送开关已开启；尚未测试，不能确认微信送达。':'已保存SendKey，微信通知当前未启用。') : '尚未配置微信通知。';
}
function updateDesktopStatus() {
  if (!$('#desktopAlertStatus')) return;
  $('#desktopAlertStatus').textContent= !('Notification' in window) ? '当前浏览器不支持桌面通知，网站内告警和微信通知仍可使用。' : Notification.permission==='granted' ? '桌面通知已启用；网页关闭后请使用微信通知。' : Notification.permission==='denied' ? '桌面通知已被浏览器阻止，可在站点权限中开启；微信通知不受影响。' : '点击右侧按钮授权桌面通知；未授权时仍会显示网站内告警。';
}
async function openAlerts() {
  if (!$('#alertsCenter')) { location.href='alerts.html'; return; }
  updateAlertSymbols();
  await loadAlertState();
  syncWechatFields();
  updateDesktopStatus();
  $('#alertsCenter').scrollIntoView({behavior:'smooth',block:'start'});
  $('#alertsTitle').focus({preventScroll:true});
}
function renderAlertCenter() {
  const active=alertState.rules.filter(r=>r.enabled).length+(alertState.basisRules||[]).filter(r=>r.enabled).length;
  if ($('#alertRuleCount')) $('#alertRuleCount').textContent=active;
  renderBasisRules();
  if (!$('#alertRules')) return;
  $('#alertRules').innerHTML=alertState.rules.length ? alertState.rules.map(rule=>{
    const direction={both:'上涨或下跌',up:'上涨',down:'下跌'}[rule.direction];
    const status={paused:'已暂停',interrupted:'行情中断，恢复后重新采样',monitoring:'监测中',warming:`采样中，约需${Math.max(1,Math.ceil(rule.remainingSeconds/60))}分钟`}[rule.status] || '采样中';
    return `<article class="alert-rule"><div><strong>${escapeHTML(rule.name)} <small>${escapeHTML(rule.symbol)}</small></strong><p>${rule.windowMinutes}分钟内${direction} ≥ ${rule.thresholdPct}% · 冷却${rule.cooldownMinutes}分钟</p><span class="rule-status ${rule.status==='monitoring'?'is-monitoring':''}">${status}</span></div><div class="alert-rule-actions"><button type="button" onclick="toggleAlertRule('${rule.id}',${!rule.enabled})">${rule.enabled?'暂停':'启用'}</button><button type="button" onclick="deleteAlertRule('${rule.id}')">删除</button></div></article>`;
  }).join('') : '<p class="alert-empty">尚未设置预警。选择标的、时间窗口和幅度后添加。</p>';
  const delivery={pending:'微信待发送',sent:'微信推送服务已受理',failed:'微信发送失败',disabled:'微信未启用'};
  $('#alertEvents').innerHTML=alertState.events.length ? alertState.events.map(event=>`<article class="alert-event"><strong class="${event.direction==='up'?'up':'down'}">${escapeHTML(alertMessage(event))}</strong><p>${alertTime(event.triggeredAt)}（北京） · ${delivery[event.wechatStatus] || '已记录'}</p><small>${event.kind==='basis' ? `当季 ${fmt(event.frontPrice)} · 下季 ${fmt(event.farPrice)} · 两腿报价 ${alertTime(event.quoteTime)}` : `基准价 ${fmt(event.baselinePrice)} · 实际跨度${event.actualSeconds}秒`}${event.wechatError ? ' · '+escapeHTML(event.wechatError) : ''}</small></article>`).join('') : '<p class="alert-empty">暂无触发记录。告警将显示在这里，最多保留200条。</p>';
}

function renderBasisRules() {
  const box=$('#basisRules'); if(!box || !box.querySelectorAll) return;
  box.innerHTML=(alertState.basisRules||[]).map(r=>`<article class="alert-rule"><div><strong>${r.symbol} 年化贴水</strong><p>${r.operator==='lte'?'≤':'≥'} ${r.thresholdPct}% · 冷却${r.cooldownMinutes}分钟</p><span class="rule-status">${r.enabled?'监测中（仅新鲜、同步报价有效）':'已暂停'}</span></div><div class="alert-rule-actions"><button type="button" data-basis-id="${r.id}" data-enabled="${!r.enabled}">${r.enabled?'暂停':'启用'}</button><button type="button" data-basis-delete="${r.id}">删除</button></div></article>`).join('') || '<p class="alert-empty">尚未设置贴水预警，不会自动启用规则。</p>';
  box.querySelectorAll('[data-basis-id]').forEach(b=>b.onclick=async()=>{try{await api('/api/alerts/basis-rules/'+b.dataset.basisId,{method:'PATCH',body:JSON.stringify({enabled:b.dataset.enabled==='true'})});await loadAlertState();}catch(e){toast(e.message);}});
  box.querySelectorAll('[data-basis-delete]').forEach(b=>b.onclick=async()=>{try{await api('/api/alerts/basis-rules/'+b.dataset.basisDelete,{method:'DELETE'});await loadAlertState();}catch(e){toast(e.message);}});
}
async function toggleAlertRule(id,enabled) {
  try { await api('/api/alerts/rules/'+id,{method:'PATCH',body:JSON.stringify({enabled})}); await loadAlertState(); }
  catch(error) { toast(error.message); }
}
async function deleteAlertRule(id) {
  try { await api('/api/alerts/rules/'+id,{method:'DELETE'}); await loadAlertState(); }
  catch(error) { toast(error.message); }
}
async function initAlerts() {
  $('#basisRuleForm')?.addEventListener('submit',async e=>{
    e.preventDefault();const button=$('#saveBasisRule');button.disabled=true;
    try{await api('/api/alerts/basis-rules',{method:'POST',body:JSON.stringify({symbol:$('#basisSymbol').value,operator:$('#basisOperator').value,thresholdPct:Number($('#basisThreshold').value),cooldownMinutes:Number($('#basisCooldown').value)})});await loadAlertState();toast('贴水预警规则已启用');}catch(error){toast(error.message);}finally{button.disabled=false;}
  });
  if (!$('#alertsCenter')) {
    await loadAlertState();
    if (alertPollTimer) clearInterval(alertPollTimer);
    alertPollTimer=setInterval(loadAlertState,15000);
    return;
  }
  $('#alertRuleForm').addEventListener('submit',async event=>{
    event.preventDefault();
    const button=$('#saveAlertRule');button.disabled=true;
    try {
      await api('/api/alerts/rules',{method:'POST',body:JSON.stringify({symbol:$('#alertSymbol').value,windowMinutes:Number($('#alertWindow').value),thresholdPct:Number($('#alertThreshold').value),direction:$('#alertDirection').value,cooldownMinutes:Number($('#alertCooldown').value)})});
      await loadAlertState();toast('预警规则已启用，开始采样');
    } catch(error) { toast(error.message); }
    finally { button.disabled=!Object.keys(symbolMap).length; }
  });
  $('#wechatAlertForm').addEventListener('submit',async event=>{
    event.preventDefault();
    const button=$('#saveWechatAlerts');button.disabled=true;
    try {
      await api('/api/alerts/wechat',{method:'POST',body:JSON.stringify({enabled:$('#wechatEnabled').checked,sendKey:$('#wechatSendKey').value.trim()})});
      await loadAlertState();syncWechatFields();toast('微信通知设置已保存');
    } catch(error) { toast(error.message); }
    finally { button.disabled=false; }
  });
  $('#testWechatAlerts').addEventListener('click',async()=>{
    const button=$('#testWechatAlerts');button.disabled=true;
    $('#saveWechatAlerts').disabled=true;
    $('#wechatAlertStatus').textContent='正在向Server酱发送测试请求，请稍候…';
    try {
      if(!$('#wechatEnabled').checked) throw new Error('请先勾选微信通知，再发送测试。');
      const key=$('#wechatSendKey').value.trim();
      if(key || !alertState.wechat.enabled) {
        const conf=await api('/api/alerts/wechat',{method:'POST',body:JSON.stringify({enabled:true,sendKey:key})});
        alertState.wechat=conf;$('#wechatSendKey').value='';
      }
      await api('/api/alerts/wechat/test',{method:'POST'});
      await loadAlertState();syncWechatFields();
      toast('Server酱已受理，请确认微信是否收到；未收到请检查官网消息通道');
    }
    catch(error) { $('#wechatAlertStatus').textContent='测试未完成：'+error.message;toast(error.message); }
    finally { button.disabled=false;$('#saveWechatAlerts').disabled=false; }
  });
  $('#enableDesktopAlerts').addEventListener('click',async()=>{
    if('Notification' in window) { try { await Notification.requestPermission(); } catch(_) {} }
    updateDesktopStatus();
  });
  await loadAlertState();
  updateAlertSymbols();
  syncWechatFields();
  updateDesktopStatus();
  if(alertPollTimer) clearInterval(alertPollTimer);
  alertPollTimer=setInterval(loadAlertState,15000);
}
