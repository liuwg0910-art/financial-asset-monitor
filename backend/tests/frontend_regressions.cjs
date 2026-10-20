const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const elements = new Map();
const element = key => {
  if (!elements.has(key)) elements.set(key, {textContent:'', classList:{add(){this.hidden=true;}, remove(){this.hidden=false;}}, getContext(){return {clearRect(){}};}});
  return elements.get(key);
};
const context = vm.createContext({
  location:{port:'19191',hostname:'192.168.1.10',origin:'https://market.example',protocol:'https:'},
  document:{querySelector:element,querySelectorAll:()=>[],getElementById:element,addEventListener(){}},
  window:{addEventListener(){}}, AbortController, setTimeout, clearTimeout,
  requestAnimationFrame(){},
  fetch:async()=>({ok:false,json:async()=>({detail:'未接入真实历史K线'})}),
});
vm.runInContext(fs.readFileSync('frontend/static/js/app.js','utf8'),context);
assert.equal(vm.runInContext('API.base',context),'https://market.example');
assert.equal(vm.runInContext('API.ws',context),'wss://market.example/ws');
assert.equal(vm.runInContext('escapeHTML("<img onerror=1>")',context),'&lt;img onerror=1&gt;');
assert.equal(vm.runInContext('fmt(-1234.5)',context),'-1,234.50');
assert.equal(vm.runInContext('classify("XAGUSDT")',context),'metal');
assert.equal(vm.runInContext('classify("ASTER")',context),'crypto');
assert.equal(vm.runInContext('classify("ASTERUSDT")',context),'crypto');
assert.equal(vm.runInContext('classify("NEWCOINUSDT")',context),'crypto');
assert.equal(vm.runInContext('classify("BZ=F")',context),'futures');
assert.equal(vm.runInContext('classify("GCZ26.CMX")',context),'futures');
assert.equal(vm.runInContext('classify("RB2701")',context),'futures');
assert.equal(vm.runInContext('classify("IF0")',context),'futures');
assert.equal(vm.runInContext('isDomesticFuture("IF2613")',context),false);
assert.equal(vm.runInContext('isFuture("GCZ26.NYM")',context),false);
assert.equal(vm.runInContext('isFuture("BZ=F\' onclick=alert(1)")',context),false);
assert.equal(vm.runInContext('label("BZZ26.NYM")',context),'期货');
element('#inputFilter').value='';
vm.runInContext('symbolMap={"BZ=F":{symbol:"BZ=F",name:"布伦特原油 连续合约"},AAPL:{symbol:"AAPL",name:"Apple"}}; priceMap={"BZ=F":{symbol:"BZ=F",market:"futures",unit:"美元/桶",price:100,change:1,changePct:1,updated:new Date().toISOString(),quoteTime:new Date().toISOString()}}; currentFilter="futures"; renderGrid()',context);
assert.ok(element('#cardGrid').innerHTML.includes('美元/桶'));
assert.ok(element('#cardGrid').innerHTML.includes('自动换月'));
assert.ok(!element('#cardGrid').innerHTML.includes('Apple'));
vm.runInContext('storePrice({symbol:"BTCUSDT",price:100,updated:"a"}); storePrice({symbol:"BTCUSDT",price:100,updated:"a"}); storePrice({symbol:"BTCUSDT",price:101,updated:"b"})',context);
assert.equal(vm.runInContext('priceHistory.BTCUSDT.length',context),2);
element('#quoteTable').innerHTML='global-board';
vm.runInContext('symbolMap={AAA:{symbol:"AAPL",name:"Apple"},GOLD:{symbol:"XAUUSD",name:"<Gold>"}}; priceMap={AAPL:{price:100,change:-1,changePct:-1},XAUUSD:{price:2000,change:2,changePct:.1}};updateDashboard(Object.values(symbolMap))',context);
assert.equal(element('#summaryUp').textContent,1);
assert.equal(element('#summaryDown').textContent,1);
assert.equal(element('#summaryOther').textContent,'0 / 0');
assert.equal(element('#quoteTable').innerHTML,'global-board');
vm.runInContext('symbolMap={};updateDashboard([])',context);
assert.equal(element('#distributionBar').innerHTML,'');
assert.equal(element('#quoteTable').innerHTML,'global-board');
vm.runInContext('globalMarketItems=[{name:"<SP500>",symbol:"^GSPC",category:"index",instrumentType:"index",region:"美国",unit:"点",price:5000,changePct:1,quoteTime:"2026-10-16T20:00:00Z"},{name:"黄金",symbol:"GC=F",category:"commodity",instrumentType:"futures",region:"贵金属",unit:"美元/盎司",price:2700,changePct:-1,quoteTime:"2026-10-16T20:00:00Z"}];renderGlobalMarkets()',context);
assert.ok(element('#quoteTable').innerHTML.includes('&lt;SP500&gt;'));
assert.ok(!element('#quoteTable').innerHTML.includes('GC=F'));
vm.runInContext('globalMarketCategory="commodity";renderGlobalMarkets()',context);
assert.ok(element('#quoteTable').innerHTML.includes('GC=F'));
assert.ok(element('#quoteTable').innerHTML.includes('美元/盎司'));
assert.ok(element('#quoteTable').innerHTML.includes('期货'));
(async()=>{
  await vm.runInContext('modalSymbol="00700"; loadKline("00700","1h")',context);
  assert.equal(element('#chartLoading').textContent,'未接入真实历史K线');
  assert.equal(element('#chartLoading').classList.hidden,false);
  console.log('Frontend regression checks passed (URLs, escaping, formatting, history, K-line errors).');
})().catch(e=>{console.error(e);process.exitCode=1;});
