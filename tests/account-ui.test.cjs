'use strict';
const test=require('node:test'),assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');

function fixture(){
  let now=Date.now(),timerId=0;const timers=new Map();
  class Clock extends Date{static now(){return now;}}
  function node(tag){return {tag,textContent:'',hidden:false,disabled:false,children:[],attributes:{},
    append(...children){for(const child of children){child.parent=this;this.children.push(child);}},replaceChildren(...children){this.children=[];this.append(...children);},
    setAttribute(name,value){this.attributes[name]=value;},showModal(){this.open=true;},close(){this.open=false;this.onclose?.();},remove(){if(this.parent)this.parent.children=this.parent.children.filter(n=>n!==this);}};}
  const body=node('body');
  function advance(ms){const end=now+ms;while(true){const item=[...timers].sort((a,b)=>a[1].at-b[1].at)[0];if(!item||item[1].at>end)break;now=item[1].at;timers.delete(item[0]);item[1].fn();}now=end;}
  const root=node('div'),button=node('button'),writes=[],prompts=[];
  let value={visible:true,accountKey:'a'.repeat(64),email:'test@example.test',planType:'pro',canReset:true,
    limits:[{name:'Codex',windows:[{remainingPercent:75,windowDurationMins:300,resetsAt:2000000000}]}],
    resetCredits:{availableCount:3,credits:[{id:'card-1',title:'Test reset',status:'available',resetType:'codexRateLimits',expiresAt:null}]},updatedAt:1900000000};
  let confirmed=true,consume=async()=>({outcome:'reset',account:{...value,resetCredits:{availableCount:2,credits:[]}}}),read=async()=>structuredClone(value),hidden=0;
  const context=vm.createContext({root,button,setTimeout(fn,ms){const id=++timerId;timers.set(id,{fn,at:now+ms});return id;},clearTimeout(id){timers.delete(id);},document:{createElement:node,body},Date:Clock,performance:{now:()=>now},crypto:{randomUUID:()=> '11111111-1111-4111-8111-111111111111'},
    window:{confirm:prompt=>{prompts.push(prompt);return confirmed;}},localStorage:{getItem(){return null;},setItem(){}},
    read:()=>read(),consume:body=>{writes.push(structuredClone(body));return consume(body);},onHidden:()=>hidden++});
  for(const file of ['web/shared/i18n.js','web/features/accounts/account.js'])vm.runInContext(fs.readFileSync(path.join(__dirname,'..',file),'utf8'),context);
  const panel=vm.runInContext('new AccountPanel({root,button,read,consume,onHidden,visible:()=>true})',context);
  const all=(n=root)=>[n,...n.children.flatMap(all)];
  const confirmButton=()=>body.children[0]?.children.at(-1).children[1];
  const confirmDialog=()=>{advance(5000);confirmButton()?.onclick();};
  const redeem=async(...args)=>{const result=panel.redeem(...args);if(body.children.length){if(confirmed)confirmDialog();else body.children[0].oncancel({preventDefault(){}});}await result;};
  return {panel,button,root,writes,prompts,all,body,advance,confirmButton,confirmDialog,redeem,text:()=>all().map(n=>n.textContent).join('\n'),
    value:()=>value,setValue:next=>value=next,setRead:next=>read=next,setConsume:next=>consume=next,
    confirm:next=>confirmed=next,language:()=>{vm.runInContext("BridgeI18n.setLanguage('en')",context);panel.render();}};
}

test('official account shows limits, authoritative card count and refresh stays usable',async()=>{
  const ui=fixture();await ui.panel.refresh();
  assert.equal(ui.button.hidden,false);assert.match(ui.text(),/75%/);assert.match(ui.text(),/可用数量：3/);
  assert.equal(ui.all().find(n=>n.textContent==='查看剩余额度').disabled,false);
  assert.equal(ui.all().find(n=>n.tag==='progress').value,75);
  assert.equal(ui.panel.countdown(2523),'1 天 18 小时 3 分钟');
  ui.language();assert.match(ui.text(),/Remaining 75%/);assert.match(ui.text(),/Shared account limits/);
});

test('API login, failed auth check and logout clear all previous official data',async()=>{
  const ui=fixture();await ui.panel.refresh();
  ui.setValue({visible:false});await ui.panel.refresh();
  assert.equal(ui.button.hidden,true);assert.equal(ui.text(),'');
  ui.setRead(()=>{throw Error('unavailable');});await ui.panel.refresh();assert.equal(ui.button.hidden,true);
});

test('a delayed official-account response cannot reappear after logout',async()=>{
  const ui=fixture();let finish;ui.setRead(()=>new Promise(resolve=>finish=resolve));
  const refresh=ui.panel.refresh();ui.panel.clear();finish(ui.value());await refresh;
  assert.equal(ui.button.hidden,true);assert.equal(ui.text(),'');
});

test('signing in again starts a fresh read while an old request is still pending',async()=>{
  const ui=fixture();let finishOld,finishNew;
  ui.setRead(()=>new Promise(resolve=>finishOld=resolve));
  const old=ui.panel.refresh();ui.panel.clear();
  ui.setRead(()=>new Promise(resolve=>finishNew=resolve));
  const fresh=ui.panel.refresh();assert.equal(typeof finishNew,'function');
  finishOld(ui.value());await old;
  assert.equal(ui.button.hidden,true);assert.equal(ui.panel.loading,true);
  finishNew({...ui.value(),accountKey:'b'.repeat(64)});await fresh;
  assert.equal(ui.button.hidden,false);assert.equal(ui.panel.value.accountKey,'b'.repeat(64));
});

test('cancelling confirmation sends nothing; repeated click while pending sends once',async()=>{
  const ui=fixture();await ui.panel.refresh();ui.confirm(false);await ui.redeem('card-1');
  assert.equal(ui.writes.length,0);ui.confirm(true);
  let finish;ui.setConsume(()=>new Promise(resolve=>finish=resolve));
  const pending=ui.panel.redeem('card-1');await ui.panel.redeem('card-1');assert.equal(ui.writes.length,0);
  assert.match(ui.all(ui.body).map(n=>n.textContent).join(' '),/test@example.test.*Test reset/);
  assert.equal(ui.confirmButton().disabled,true);ui.confirmButton().onclick();assert.equal(ui.writes.length,0);
  ui.advance(4999);ui.confirmButton().onclick();assert.equal(ui.writes.length,0);
  ui.advance(1);ui.confirmButton().onclick();await Promise.resolve();assert.equal(ui.writes.length,1);
  await ui.panel.redeem('card-1');assert.equal(ui.writes.length,1);
  finish({outcome:'reset',account:{...ui.value(),resetCredits:{availableCount:2,credits:[]}}});await pending;
  assert.equal(ui.writes[0].confirmed,true);assert.match(ui.text(),/已使用重置卡/);assert.match(ui.text(),/可用数量：2/);
});

test('uncertain reset retry keeps the backend persisted idempotency key',async()=>{
  const ui=fixture();await ui.panel.refresh();ui.setConsume(body=>{
    ui.setValue({...ui.value(),pendingReset:{requestId:body.requestId,accountKey:body.accountKey,creditId:body.creditId}});
    throw Error('connection lost');
  });
  await ui.redeem('card-1');assert.match(ui.text(),/重试原请求/);
  ui.setConsume(()=>({outcome:'alreadyRedeemed',account:{...ui.value(),pendingReset:null}}));
  await ui.redeem('card-1',true);
  assert.equal(ui.writes.length,2);assert.equal(ui.writes[0].requestId,ui.writes[1].requestId);
  assert.match(ui.text(),/此重置已完成/);
});

test('permission disabled, no cards and expired cards disable use without changing settings',async()=>{
  const ui=fixture();ui.setValue({...ui.value(),canReset:false});await ui.panel.refresh();
  assert.equal(ui.all().find(n=>n.textContent==='使用重置卡').disabled,true);
  await ui.redeem('card-1');assert.equal(ui.writes.length,0);
  ui.setValue({...ui.value(),canReset:true,resetCredits:{availableCount:0,credits:[]}});await ui.panel.refresh();
  assert.equal(ui.all().some(n=>n.textContent==='使用重置卡'),false);
});

test('missing count and windows show unavailable, never zero; count-only resets are supported',async()=>{
  const ui=fixture();ui.setValue({...ui.value(),limits:[{name:'Codex',windows:[{remainingPercent:null}]}],resetCredits:null});await ui.panel.refresh();
  assert.match(ui.text(),/暂未提供/);assert.doesNotMatch(ui.text(),/0%|可用数量：0/);
  ui.setValue({...ui.value(),resetCredits:{availableCount:2,credits:null}});await ui.panel.refresh();
  assert.ok(ui.all().some(n=>n.textContent==='使用一张重置卡'));await ui.redeem(null);
  assert.equal(ui.writes[0].creditId,null);
});

test('account switching during a reset removes the old result and pending card',async()=>{
  const ui=fixture();await ui.panel.refresh();ui.setConsume(()=>({outcome:'reset',account:{...ui.value(),accountKey:'b'.repeat(64),email:'other@example.test'}}));
  await ui.redeem('card-1');assert.match(ui.text(),/other@example.test/);assert.doesNotMatch(ui.text(),/已使用重置卡/);
  assert.equal(ui.panel.pending,null);
});

test('refresh keeps the snapshot visible, blocks reset and retains it on query failure',async()=>{
 const ui=fixture();await ui.panel.refresh();let reject;ui.setRead(()=>new Promise((r,j)=>reject=j));const pending=ui.panel.refresh();
 assert.match(ui.text(),/查询中/);assert.match(ui.text(),/剩余 75%/);await ui.redeem('card-1');assert.equal(ui.writes.length,0);
 reject(Error('unavailable'));await pending;assert.match(ui.text(),/剩余 75%/);assert.match(ui.text(),/保留上次结果/);assert.equal(ui.panel.value.canReset,false);
 ui.setRead(async()=>({...ui.value(),limits:[{name:'Codex',windows:[{remainingPercent:60}]}]}));await ui.panel.refresh();assert.match(ui.text(),/剩余 60%/);assert.doesNotMatch(ui.text(),/剩余 75%|保留上次结果/);assert.equal(ui.panel.value.canReset,true);
});


test('expiry during the five-second confirmation prevents any consumption',async()=>{
  const ui=fixture();ui.setValue({...ui.value(),resetCredits:{availableCount:1,credits:[{...ui.value().resetCredits.credits[0],expiresAt:Date.now()/1000+3}]}});await ui.panel.refresh();
  const pending=ui.panel.redeem('card-1');ui.advance(5000);await pending;assert.equal(ui.writes.length,0);assert.equal(ui.body.children.length,0);
});
test('account change, permission loss and logout cancel an open confirmation',async()=>{
  for(const change of ['account','permission','logout']){
    const ui=fixture();await ui.panel.refresh();const pending=ui.panel.redeem('card-1');
    if(change==='logout')ui.panel.clear();else ui.panel.accept({...ui.value(),...(change==='account'?{accountKey:'b'.repeat(64)}:{canReset:false})});
    ui.advance(5000);await pending;assert.equal(ui.writes.length,0);assert.equal(ui.body.children.length,0);
  }
});
test('expired cards remain visible and cannot be redeemed, including direct invocation',async()=>{
  const ui=fixture();ui.setValue({...ui.value(),resetCredits:{availableCount:0,credits:[{...ui.value().resetCredits.credits[0],expiresAt:1}]}});await ui.panel.refresh();
  assert.ok(ui.all().find(n=>n.textContent==='已过期')?.disabled);await ui.panel.redeem('card-1');assert.equal(ui.body.children.length,0);assert.equal(ui.writes.length,0);
});
test('an expired uncertain request can only retry its original idempotency key',async()=>{
  const ui=fixture();const pendingReset={requestId:'old-request',accountKey:ui.value().accountKey,creditId:'card-1'};
  ui.setValue({...ui.value(),pendingReset,resetCredits:{availableCount:0,credits:[{...ui.value().resetCredits.credits[0],expiresAt:1}]}});await ui.panel.refresh();
  await ui.redeem('card-1',true);assert.equal(ui.writes[0].requestId,'old-request');
});


test('login subscription dates never claim current expiry and old cached dates stay unknown',async()=>{
 const ui=fixture();
 for(const subscription of [null,{periodEndsAt:1790147934,source:'login'},{periodEndsAt:1788250708,source:'login'}]){
  ui.setValue({...ui.value(),subscription});await ui.panel.refresh();
  assert.match(ui.text(),/订阅有效期暂未确认/);assert.doesNotMatch(ui.text(),/当前订阅周期|已到记录日期|后结束/);
 }
 ui.setValue({...ui.value(),subscription:{periodEndsAt:Date.now()/1000+86400,source:'login'}});await ui.panel.refresh();
 assert.match(ui.text(),/登录记录中的订阅日期（非实时）：/);assert.match(ui.text(),/请以 ChatGPT 订阅页面为准/);
 assert.doesNotMatch(ui.text(),/当前订阅周期|后结束/);
 ui.advance(86401000);ui.panel.render();assert.match(ui.text(),/订阅有效期暂未确认/);
 ui.language();assert.match(ui.text(),/Subscription end date is unconfirmed/);assert.match(ui.text(),/ChatGPT subscription page/);
});


test('online subscription date is distinct from stale login and failure is not expiry',async()=>{
 const ui=fixture();ui.setValue({...ui.value(),subscription:{periodEndsAt:2000000000,source:'online'}});await ui.panel.refresh();
 assert.match(ui.text(),/查询到的订阅周期截止日期/);assert.doesNotMatch(ui.text(),/登录记录中的订阅日期/);
 ui.setValue({...ui.value(),subscription:{periodEndsAt:null,source:'online',error:'订阅信息暂不可用，请稍后刷新'}});await ui.panel.refresh();
 assert.match(ui.text(),/暂未确认/);assert.doesNotMatch(ui.text(),/已过期/);ui.language();assert.match(ui.text(),/Subscription details are unavailable/);
});
