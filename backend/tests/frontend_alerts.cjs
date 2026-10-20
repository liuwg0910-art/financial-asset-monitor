const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const elements=new Map();
const element=key=>{
  if(!elements.has(key)) elements.set(key,{textContent:'',innerHTML:'',value:'',hidden:true,style:{},dataset:{},classList:{add(){},remove(){}},setAttribute(){},focus(){},addEventListener(){}});
  return elements.get(key);
};
const storage=new Map();let notifications=0;
class NotificationMock{static permission='granted';constructor(){notifications++;}}
const context=vm.createContext({document:{querySelector:element,querySelectorAll:()=>[],getElementById:element,addEventListener(){}},window:{Notification:NotificationMock,addEventListener(){}},Notification:NotificationMock,location:{origin:'http://localhost',port:'19192',hostname:'localhost'},localStorage:{getItem:k=>storage.get(k),setItem:(k,v)=>storage.set(k,v)},setTimeout:()=>0,clearTimeout(){},setInterval:()=>0,clearInterval(){}});
vm.runInContext(fs.readFileSync('frontend/static/js/app.js','utf8'),context);
vm.runInContext(fs.readFileSync('frontend/static/js/alerts.js','utf8'),context);
const event={id:'alert1',symbol:'BTCUSDT',name:'<比特币>',windowMinutes:5,thresholdPct:1,direction:'up',changePct:2,price:102,baselinePrice:100,baselineTime:'2026-10-18T00:00:00Z',triggeredAt:'2026-10-18T00:05:00Z',actualSeconds:300,wechatStatus:'pending'};
context.testEvent=event;
vm.runInContext('receiveAlertEvent(testEvent); receiveAlertEvent(testEvent)',context);
assert.equal(notifications,1);
assert.equal(vm.runInContext('alertState.events.length',context),1);
assert.equal(element('#alertBanner').hidden,false);
assert.ok(element('#alertEvents').innerHTML.includes('&lt;比特币&gt;'));
vm.runInContext('updateAlertDelivery({id:"alert1",wechatStatus:"sent"})',context);
assert.equal(notifications,1);
assert.ok(element('#alertEvents').innerHTML.includes('微信推送服务已受理'));
vm.runInContext('receiveAlertEvent({...testEvent,id:"old-event"},false)',context);
assert.equal(notifications,1);
assert.equal(JSON.parse(storage.get('financial-asset-monitor-alert-seen')).length,2);
console.log('Alert frontend checks passed: one notification per event, history escaping, delivery updates, old-event suppression.');
