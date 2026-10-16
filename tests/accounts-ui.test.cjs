'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
function fixture(desktop=false){
  function node(tag){return {tag,textContent:'',value:'',hidden:false,disabled:false,children:[],attributes:{},classList:{add(){}},
    append(...children){for(const child of children){child.parent=this;this.children.push(child);}},replaceChildren(...children){this.children=children;},setAttribute(k,v){this.attributes[k]=v;},closest(){return null;},reset(){},focus(){},showModal(){this.open=true;},close(){this.open=false;this.onclose?.();},remove(){if(this.parent)this.parent.children=this.parent.children.filter(n=>n!==this);}};}
  const root=node('div'),requests=[],prompts=[],notifications=[];let confirmed=true,value={accounts:[{id:'a',name:'API <script>',kind:'api',baseUrl:'https://example.test/v1',model:'model'}],activeId:null,switch:{phase:'idle'},canSwitch:true};
  let request=async()=>structuredClone(value),read=async()=>structuredClone(value),timers=new Map(),timerId=0,now=Date.now();
  class Clock extends Date{static now(){return now;}}
  const context=vm.createContext({document:{createElement:node,body:node('body'),hidden:false,activeElement:null},root,URL,Date:Clock,crypto:{randomUUID:()=> '11111111-1111-4111-8111-111111111111'},
    setTimeout:(fn,delay)=>{timers.set(++timerId,{fn,delay});return timerId;},clearTimeout:id=>timers.delete(id),localStorage:{getItem(){return null;},setItem(){}},window:{confirm:message=>{prompts.push(message);return confirmed;},prompt:()=>null}});
  for(const f of ['web/shared/i18n.js','web/features/accounts/account.js','web/features/accounts/accounts.js'])vm.runInContext(fs.readFileSync(path.join(__dirname,'..',f),'utf8'),context);
  const Class=vm.runInContext('AccountsPanel',context),panel=new Class({root,desktop,notify:message=>notifications.push(message),read:refresh=>read(refresh),request:v=>{requests.push(v);return request(v);}});
  const all=(n=root)=>[n,...n.children.flatMap(all)];
  return {panel,root,requests,prompts,notifications,all,timers,runTimer:()=>timers.get(panel.timer)?.fn(),now:()=>now,advance:ms=>now+=ms,hidden:value=>context.document.hidden=value,noRandomUUID:()=>{context.crypto={getRandomValues:bytes=>require("node:crypto").randomFillSync(bytes)};},text:()=>all().map(n=>n.textContent).join('\n'),setValue:v=>value=v,value:()=>value,setRequest:v=>request=v,setRead:v=>read=v,confirm:v=>confirmed=v,
    language:()=>{vm.runInContext("BridgeI18n.setLanguage('en')",context);panel.render();}};
}
for(const desktop of [false,true])test(`${desktop?'desktop':'web'} global reset is compact and independent of personal quota`,async()=>{
 const ui=fixture(desktop),now=ui.now()/1000;
 const globalReset={state:'ready',updatedAt:now,lastResetAt:now-120,probabilities:{h24:19,h48:34},confidence:'low'};
 const account={id:'one',kind:'chatgpt',name:'Work',details:{usage:{status:'ready',checkedAt:now,limits:[{name:'Codex',windows:[{remainingPercent:8,windowDurationMins:10080}]}]}}};
 ui.panel.accept({...ui.value(),globalReset,accounts:[account]});
 assert.match(ui.text(),/全局已重置 · 今天 /);assert.match(ui.text(),/剩余 8%/);
 assert.equal(ui.all(ui.panel.globalReset).filter(n=>n.tag==='button').length,2);
 assert.equal(ui.all(ui.panel.globalReset).some(n=>/100%|额度已恢复|刷新|重置记录|19%/.test(n.textContent)),false);
 assert.equal(ui.all(ui.panel.globalReset).find(n=>n.tag==='a').href,'https://codex-reset.com/zh/');
 account.details.usage.limits[0].windows[0].remainingPercent=100;
 ui.panel.accept({...ui.value(),globalReset,accounts:[account]});
 assert.match(ui.text(),/剩余 100%/);assert.match(ui.text(),/全局已重置 · 今天 /);
});

test('reset refresh stays available on failure, coalesces clicks and leaves quota queries alone',async()=>{
 const ui=fixture();ui.setValue({...ui.value(),globalReset:{state:'error'},accounts:[{id:'official',kind:'chatgpt',name:'Work'}]});ui.panel.accept(ui.value());
 const find=()=>ui.all(ui.panel.globalReset).find(n=>n.className==='accounts-global-refresh');
 assert.ok(find());assert.equal(find().disabled,false);assert.equal(find().attributes['aria-label'],'刷新重置动态');
 let finish;const forces=[];ui.setRead(force=>{forces.push(force);return new Promise(resolve=>finish=resolve);});
 const pending=find().onclick();assert.equal(find().disabled,true);assert.equal(find().attributes['aria-busy'],'true');
 await find().onclick();assert.equal(forces.length,1);
 finish({...ui.value(),globalReset:{state:'ready',updatedAt:ui.now()/1000,probabilities:{h24:19}}});await pending;
 assert.deepEqual(forces,[true]);assert.equal(ui.requests.length,0);assert.equal(find().disabled,false);assert.equal(find().attributes['aria-busy'],'false');
 ui.setRead(async()=>{throw Error('连接失败');});await find().onclick();assert.equal(find().disabled,false);
 ui.language();assert.equal(find().attributes['aria-label'],'Refresh reset updates');
});

test('manual refresh waits for the external job and reports its actual result once',async()=>{
 for(const changed of [false,true]){
  const ui=fixture(),now=ui.now()/1000;
  const globalReset={state:'ready',updatedAt:now,checkedAt:now,refresh:{id:1,checking:false,finishedAt:now,changed:false,cooldownUntil:0,retryAt:0}};
  ui.setValue({...ui.value(),globalReset});ui.panel.accept(ui.value());
  ui.setRead(async()=>({...ui.value(),globalReset:{...globalReset,refresh:{...globalReset.refresh,id:2,checking:true}}}));
  await ui.panel.refreshGlobalReset();
  const find=()=>ui.all().find(n=>n.className==='accounts-global-refresh');
  assert.equal(find().attributes['aria-busy'],'true');assert.equal(ui.notifications.length,0);
  ui.panel.accept(ui.value());assert.equal(ui.notifications.length,0);
  ui.panel.accept({...ui.value(),globalReset:{...globalReset,checkedAt:now+10,refresh:{...globalReset.refresh,id:2,checking:false,finishedAt:now+10,changed}}});
  assert.equal(find().attributes['aria-busy'],'false');assert.equal(ui.notifications.at(-1),changed?'已更新':'已检查，暂无新动态');
  assert.equal(ui.notifications.length,1);assert.equal(ui.requests.length,0);
  ui.panel.render();assert.equal(ui.notifications.length,1);
  assert.ok(ui.all().some(n=>n.title?.startsWith('本次检查于 ')));
 }
});

test('gateway restart ends old refresh waits even when task IDs restart from zero',async()=>{
 const ui=fixture(),now=ui.now()/1000,globalReset={state:'ready',updatedAt:now,refresh:{instanceId:'first',id:6,checking:false}};
 ui.panel.accept({...ui.value(),globalReset});
 ui.setRead(async()=>({...ui.value(),globalReset:{...globalReset,refresh:{instanceId:'first',id:7,checking:true}}}));
 await ui.panel.refreshGlobalReset();assert.equal(ui.panel.globalResetRefreshIntent.id,7);
 ui.panel.accept({...ui.value(),globalReset:{...globalReset,refresh:{instanceId:'second',id:0,checking:false}}});
 assert.equal(ui.panel.globalResetRefreshIntent,null);
 assert.equal(ui.all().find(n=>n.className==='accounts-global-refresh').disabled,false);
 assert.equal(ui.notifications.at(-1),'网关已重连，请重新刷新');
});

test('cooldowns, failures and instant completion give explicit refresh feedback',async()=>{
 const ui=fixture(),now=ui.now()/1000,base={state:'ready',updatedAt:now,refresh:{id:1,checking:false,finishedAt:now,cooldownUntil:now+45,retryAt:0,changed:false}};
 for(const [response,expected] of [[base,/45 秒后可刷新/],
  [{...base,state:'error',refresh:{...base.refresh,retryAt:now+1800}},/30 分钟后可重试/],
  [{...base,refresh:{...base.refresh,id:2,changed:true}},/^已更新$/]]){
  ui.panel.accept({...ui.value(),globalReset:base});ui.setRead(async()=>({...ui.value(),globalReset:response}));await ui.panel.refreshGlobalReset();
  assert.match(ui.notifications.at(-1),expected);assert.equal(ui.panel.globalResetRefreshIntent,null);
 }
 ui.setRead(async()=>{throw Error('network');});await ui.panel.refreshGlobalReset();assert.equal(ui.notifications.at(-1),'连接失败，请稍后重试');assert.equal(ui.panel.globalResetRefreshIntent,null);
});

test('reset and quota refresh icons share the same SVG',()=>{
 const ui=fixture(),now=ui.now()/1000;
 ui.panel.accept({...ui.value(),globalReset:{state:'ready',updatedAt:now},accounts:[{id:'one',kind:'chatgpt',name:'Work'}]});
 assert.equal(ui.all().find(n=>n.className==='accounts-global-refresh').innerHTML,ui.all().find(n=>n.className==='accounts-usage-refresh').innerHTML);
});

test('regular status polling keeps the reset refresh button stable',async()=>{
 const ui=fixture();ui.setValue({...ui.value(),globalReset:{state:'ready',updatedAt:ui.now()/1000}});ui.panel.accept(ui.value());
 const find=()=>ui.all(ui.panel.globalReset).find(n=>n.className==='accounts-global-refresh'),button=find();
 let finish;ui.setRead(()=>new Promise(resolve=>finish=resolve));
 for(let i=0;i<3;i++){
  const pending=ui.panel.refresh();
  assert.equal(find(),button);assert.equal(button.disabled,false);assert.equal(button.attributes['aria-busy'],'false');
  finish(structuredClone(ui.value()));await pending;
  assert.equal(find(),button);assert.equal(button.disabled,false);assert.equal(button.attributes['aria-busy'],'false');
 }
});

test('opening requests a fresh outlook once while subsequent polling reuses cache',async()=>{
 const ui=fixture(),forces=[];ui.setRead(async force=>{forces.push(force);return ui.value();});
 await ui.panel.refresh(true);await ui.panel.refresh();
 assert.deepEqual(forces,[true,false]);
 let finish;ui.setRead(force=>{forces.push(force);return new Promise(resolve=>finish=resolve);});
 const pending=ui.panel.refresh();await ui.panel.refresh(true);finish(ui.value());await pending;
 ui.setRead(async force=>{forces.push(force);return ui.value();});await ui.panel.refresh();
 assert.equal(forces.at(-1),true);
});

test('hidden desktop client configuration stops quota loading and scheduled refreshes',async()=>{
 const ui=fixture(true);let hidden=true,reads=0;
 ui.root.closest=selector=>selector.includes('.client-config-hidden')&&hidden?ui.root:null;
 ui.setValue({...ui.value(),accounts:[{id:'official',kind:'chatgpt',name:'Work'}]});
 ui.setRead(async()=>{reads++;return ui.value();});
 assert.equal(ui.panel.visible(),false);
 await ui.panel.refresh();assert.equal(reads,1);assert.equal(ui.requests.length,0);
 ui.runTimer();assert.equal(reads,1);
 hidden=false;assert.equal(ui.panel.visible(),true);
 await ui.panel.loadUsage(ui.panel.generation);assert.equal(ui.requests.length,1);
});

test('desktop navigation refreshes only the selected visible Codex configuration',()=>{
 const source=fs.readFileSync(path.join(__dirname,'../desktop/shell/renderer.js'),'utf8');
 const start=source.indexOf('function moduleName('),end=source.indexOf("\ndocument.querySelectorAll('[data-tab]')",start);
 let selected=false,settingsReads=0;const reads=[],section={dataset:{panel:'accounts'},hidden:true};
 const context=vm.createContext({activeTab:'overview',snapshot:null,titles:{accounts:'Clients',notifications:'Notifications'},t:value=>value,$:()=>({}),
  desktopConnections:null,harnessPanel:null,accountSettings:{refresh(){settingsReads++;},schedule(){}},
  accountsPanel:{visible:()=>selected&&!section.hidden,refresh:force=>reads.push(force),schedule(){}},
  window:{GatewayLayout:{tab(){}}},document:{querySelectorAll:selector=>selector==='[data-panel]'?[section]:[]}});
 vm.runInContext(source.slice(start,end),context);
 vm.runInContext("tab('accounts')",context);assert.deepEqual(reads,[]);
 selected=true;vm.runInContext("tab('accounts')",context);assert.deepEqual(reads,[true]);assert.equal(settingsReads,0);
 vm.runInContext("tab('notifications')",context);assert.deepEqual(reads,[true]);assert.equal(settingsReads,1);
 vm.runInContext("tab('accounts')",context);assert.deepEqual(reads,[true,true]);assert.equal(settingsReads,1);
});

test('desktop Codex configuration changes use one account read and preserve notification reads',()=>{
 const source=fs.readFileSync(path.join(__dirname,'../desktop/shell/renderer.js'),'utf8');
 const line=source.split('\n').find(line=>line.startsWith("document.addEventListener('bridge-client-config'"));
 let listener,visible=true,reads=0,settingsReads=0,schedules=0;
 const context=vm.createContext({document:{addEventListener:(name,fn)=>listener=fn},
  accountsPanel:{visible:()=>visible,refresh:()=>reads++,schedule:()=>schedules++},
  accountSettings:{refresh:()=>settingsReads++,schedule:()=>schedules++}});
 vm.runInContext(line,context);listener({detail:'codex'});
 assert.equal(reads,1);assert.equal(settingsReads,0);
 visible=false;listener({detail:'codex'});listener({detail:'claude'});
 assert.equal(reads,1);assert.equal(schedules,4);
});

test('idle accounts retain nodes and refresh at cache expiry while active jobs use short polling',async()=>{
 const ui=fixture();await ui.panel.refresh();const first=ui.panel.rows.children[0];
 assert.equal(ui.timers.get(ui.panel.timer).delay,300000);
 await ui.panel.refresh();assert.equal(ui.panel.rows.children[0],first);
 ui.setValue({...ui.value(),switch:{phase:'starting'}});await ui.panel.refresh();assert.equal(ui.timers.get(ui.panel.timer).delay,2000);
 ui.setValue({...ui.value(),switch:{phase:'complete'}});await ui.panel.refresh();assert.equal(ui.timers.get(ui.panel.timer).delay,300000);
 ui.hidden(true);ui.panel.schedule();assert.equal(ui.timers.size,0);
});

test('quota deadlines update locally without requests or rebuilding account nodes',async()=>{
 const ui=fixture(),now=ui.now()/1000;
 ui.setValue({...ui.value(),accounts:[{id:'official',kind:'chatgpt',name:'Work',details:{usage:{status:'ready',checkedAt:now,limits:[{name:'Codex',windows:[{remainingPercent:75,windowDurationMins:300,resetsAt:now+3600}]}]}}}]});
 await ui.panel.refresh();const card=ui.panel.rows.children[0],label=ui.all().find(node=>node.className==='account-reset-time'),before=label.textContent;
 ui.advance(60000);const tick=ui.timers.get(ui.panel.clockTimer);ui.timers.delete(ui.panel.clockTimer);tick.fn();
 assert.notEqual(label.textContent,before);assert.equal(ui.panel.rows.children[0],card);assert.equal(ui.requests.length,0);
 ui.panel.clear();assert.equal(ui.timers.size,0);
});

test('hidden account snapshots and bell changes preserve the cached list until shown',async()=>{
 const ui=fixture();await ui.panel.refresh();const first=ui.panel.rows.children[0];ui.hidden(true);
 ui.panel.accept({...ui.value(),accounts:[{...ui.value().accounts[0],name:'Changed'}]});
 ui.panel.acceptReminders({reminders:{globalReset:true},accounts:[]});
 assert.equal(ui.panel.rows.children[0],first);assert.equal(ui.panel.value.accounts[0].name,'Changed');
 ui.hidden(false);ui.panel.render();assert.notEqual(ui.panel.rows.children[0],first);assert.match(ui.text(),/Changed/);
});

test('new official rows and deferred manual refreshes are followed promptly',async()=>{
 const ui=fixture();await ui.panel.refresh();
 ui.setRequest(async()=>({...ui.value(),accounts:[{id:'new',kind:'chatgpt',name:'New'}]}));
 await ui.panel.perform({action:'import',candidateId:'fixture'});assert.equal(ui.timers.get(ui.panel.timer).delay,2000);
 ui.panel.accept(ui.value());ui.panel.busy=true;await ui.panel.refresh(true);ui.panel.busy=false;ui.panel.schedule();
 assert.equal(ui.timers.get(ui.panel.timer).delay,2000);
});

for(const reopen of [false,true])test(`leaving a quota batch stops unsent requests${reopen?' across a quick reopen':''}`,async()=>{
 const ui=fixture(),accounts=Array.from({length:5},(_,i)=>({id:'row'+i,kind:'chatgpt',name:'Account '+i}));
 ui.setValue({...ui.value(),accounts});ui.panel.accept(ui.value());let finish;
 ui.setRequest(()=>new Promise(resolve=>finish=resolve));
 const pending=ui.panel.loadUsage(ui.panel.generation);assert.equal(ui.requests.length,1);
 ui.hidden(true);ui.panel.schedule();if(reopen)ui.hidden(false);
 finish({...ui.value(),accounts:accounts.slice(1)});await pending;
 assert.equal(ui.requests.length,1);assert.equal(ui.panel.value.accounts.length,5);
});

test('quota batches render once while retaining every returned snapshot',async()=>{
 const ui=fixture(),accounts=Array.from({length:20},(_,i)=>({id:'row'+i,kind:'chatgpt',name:'Account '+i}));
 ui.setValue({...ui.value(),accounts});ui.panel.accept(ui.value());
 let builds=0;const replace=ui.panel.rows.replaceChildren.bind(ui.panel.rows);
 ui.panel.rows.replaceChildren=(...nodes)=>{builds++;replace(...nodes);};
 ui.setRequest(async payload=>{
  assert.equal(builds,0);
  const value=structuredClone(ui.value());value.accounts.find(row=>row.id===payload.id).details={usage:{status:'loading'}};
  ui.setValue(value);return value;
 });
 await ui.panel.loadUsage(ui.panel.generation);
 assert.equal(ui.requests.length,20);assert.equal(builds,1);
 assert.equal(ui.panel.value.accounts.filter(row=>row.details?.usage?.status==='loading').length,20);
});

test('partial quota batches render successful progress when a later request fails',async()=>{
 const ui=fixture(),accounts=Array.from({length:3},(_,i)=>({id:'row'+i,kind:'chatgpt',name:'Account '+i}));
 ui.setValue({...ui.value(),accounts});ui.panel.accept(ui.value());let builds=0;
 const replace=ui.panel.rows.replaceChildren.bind(ui.panel.rows);ui.panel.rows.replaceChildren=(...nodes)=>{builds++;replace(...nodes);};
 ui.setRequest(async()=>{
  if(ui.requests.length===2)throw Error('Connection lost');
  const value=structuredClone(ui.value());value.accounts[0].details={usage:{status:'loading'}};return value;
 });
 await assert.rejects(ui.panel.loadUsage(ui.panel.generation),/Connection lost/);
 assert.equal(ui.requests.length,2);assert.equal(builds,1);
 assert.equal(ui.panel.value.accounts[0].details.usage.status,'loading');
});

test('deferred quota renders still close reset dialogs immediately on identity changes',()=>{
 const ui=fixture(),row={id:'current-login',kind:'chatgpt',name:'Native',transient:true,accountKey:'first'};
 ui.panel.accept({...ui.value(),loginAccount:row});
 const dialog={open:true,close(){this.open=false;}};ui.panel.resetDialog=dialog;ui.panel.resetAccountId=row.id;ui.panel.resetAccountKey=row.accountKey;
 ui.panel.accept({...ui.value(),loginAccount:{...row,accountKey:'second'}},false);
 assert.equal(dialog.open,false);
});

test('a switch starting during quota dispatch ends the batch and renders protection promptly',async()=>{
 const ui=fixture(),accounts=Array.from({length:3},(_,i)=>({id:'row'+i,kind:'chatgpt',name:'Account '+i}));
 ui.setValue({...ui.value(),accounts});ui.panel.accept(ui.value());
 ui.setRequest(async()=>({...ui.value(),switch:{phase:'preparing'}}));
 await ui.panel.loadUsage(ui.panel.generation);
 assert.equal(ui.requests.length,1);assert.match(ui.text(),/正在准备账号/);
});

test('interrupted switches stay idle and failed reads retry with a short delay',async()=>{
 const ui=fixture();ui.setValue({...ui.value(),switch:{phase:'interrupted'},accounts:[{id:'official',kind:'chatgpt',name:'Work'}]});
 await ui.panel.refresh();assert.equal(ui.timers.get(ui.panel.timer).delay,300000);assert.equal(ui.requests.length,0);
 ui.panel.accept({...ui.value(),switch:{phase:'idle'},accounts:[{id:'official',kind:'chatgpt',name:'Work',details:{usage:{status:'ready',checkedAt:1}}}]});
 ui.setRead(async()=>{throw Error('Offline');});await ui.panel.refresh();assert.equal(ui.timers.get(ui.panel.timer).delay,10000);
});

test('community odds show only when fresh and no active announcement exists',()=>{
 const ui=fixture(),now=ui.now()/1000;
 const globalReset={state:'ready',updatedAt:now,lastResetAt:now-90000,probabilities:{h24:19,h48:null},confidence:'low'};
 ui.panel.accept({...ui.value(),globalReset});
 assert.match(ui.text(),/19%/);assert.match(ui.text(),/社区预测 · 低置信度/);assert.equal(ui.text().includes('null%'),false);
 ui.panel.accept({...ui.value(),globalReset:{...globalReset,officialSignal:{at:now,endsAt:now+3600}}});
 assert.match(ui.text(),/官方已预告/);assert.match(ui.text(),/前重置/);assert.equal(ui.text().includes('19%'),false);
 ui.panel.accept({...ui.value(),globalReset:{...globalReset,stale:true}});
 assert.match(ui.text(),/数据延迟/);assert.equal(ui.text().includes('19%'),false);
 ui.panel.accept({...ui.value(),globalReset:{state:'error'}});
 assert.match(ui.text(),/重置动态暂时加载失败/);
});

test('official announcements preserve original text, expand and link to the matching post',()=>{
 const ui=fixture(),now=ui.now()/1000,original='Original text <script> stays plain.\n'.repeat(8),url='https://x.com/thsottiaux/status/123';
 const signal={at:now-60,text:original,summary:'Short source summary',url};
 ui.panel.accept({...ui.value(),globalReset:{state:'ready',updatedAt:now,officialSignal:signal}});
 assert.equal(ui.all().find(n=>n.className==='accounts-global-state').textContent,'官方已预告 · 即将重置');assert.match(ui.text(),/公告原文/);assert.doesNotMatch(ui.text(),/具体时间待公布|Short source summary|来源摘要/);
 const body=()=>ui.all().find(n=>n.className==='accounts-global-announcement-text'),toggle=()=>ui.all().find(n=>n.className==='accounts-global-expand');
 assert.equal(body().textContent,original);assert.equal(body().attributes['data-expanded'],'false');
 assert.equal(ui.all().find(n=>n.textContent==='查看原帖 ↗').href,url);
 toggle().onclick();assert.equal(body().attributes['data-expanded'],'true');
 ui.panel.busy=true;ui.panel.render();assert.equal(body().attributes['data-expanded'],'true');
 toggle().onclick();assert.equal(body().attributes['data-expanded'],'false');
 ui.language();assert.match(ui.text(),/Original post/);assert.equal(body().textContent,original);
});

test('Chinese announcements default to the source translation and can switch to original',()=>{
 const ui=fixture(),now=ui.now()/1000,signal={at:now-60,text:'Landing within the next 2 hours',translation:{locale:'zh',text:'将在接下来的 2 小时内着陆'},url:'https://x.com/thsottiaux/status/123'};
 const globalReset={state:'ready',updatedAt:now,officialSignal:signal};
 ui.panel.accept({...ui.value(),globalReset});
 const body=()=>ui.all().find(n=>n.className==='accounts-global-announcement-text'),toggle=()=>ui.all().find(n=>n.className==='accounts-global-language');
 assert.equal(body().textContent,signal.translation.text);assert.match(ui.text(),/机器翻译/);assert.equal(toggle().textContent,'查看原文');
 toggle().onclick();assert.equal(body().textContent,signal.text);assert.match(ui.text(),/公告原文/);assert.equal(toggle().textContent,'查看译文');
 ui.panel.accept({...ui.value(),globalReset:{...globalReset,checkedAt:now+30}});assert.equal(body().textContent,signal.text);
 toggle().onclick();assert.equal(body().textContent,signal.translation.text);
 ui.language();assert.equal(body().textContent,signal.text);assert.equal(toggle().textContent,'View translation');
 toggle().onclick();assert.equal(body().textContent,signal.translation.text);assert.match(ui.text(),/Machine translation/);
});

test('announcements without a matching Chinese translation show the original alone',()=>{
 const ui=fixture(),now=ui.now()/1000;
 for(const translation of [undefined,{locale:'ja',text:'他の翻訳'},{locale:'zh',text:''}]){
  ui.panel.accept({...ui.value(),globalReset:{state:'ready',updatedAt:now,officialSignal:{at:now-60,text:'Original announcement',translation}}});
  assert.equal(ui.all().find(n=>n.className==='accounts-global-announcement-text').textContent,'Original announcement');
  assert.equal(ui.all().some(n=>n.className==='accounts-global-language'),false);assert.doesNotMatch(ui.text(),/机器翻译/);
 }
});

test('source summaries are labelled and stale announcements stay hidden',()=>{
 const ui=fixture(),now=ui.now()/1000,globalReset={state:'ready',updatedAt:now,officialSignal:{summary:'Source supplied summary',url:'javascript:alert(1)'}};
 ui.panel.accept({...ui.value(),globalReset});assert.match(ui.text(),/来源摘要/);assert.doesNotMatch(ui.text(),/公告原文|查看原帖/);
 ui.panel.accept({...ui.value(),globalReset:{...globalReset,stale:true}});assert.doesNotMatch(ui.text(),/Source supplied summary|来源摘要/);
});

test('bell directly toggles both reminders, coalesces clicks and retains state on save failure',async()=>{
 const ui=fixture(),now=ui.now()/1000;
 ui.setValue({...ui.value(),globalReset:{state:'ready',updatedAt:now,probabilities:{h24:19}},reminders:{globalReset:false,globalResetSignal:false}});
 await ui.panel.refresh();
 const button=ui.all(ui.panel.globalReset).find(n=>n.tag==='button');
 assert.equal(button.textContent,'');assert.match(button.innerHTML,/<svg[^>]+aria-hidden="true"/);
 assert.equal(button.title,'开启重置动态提醒');assert.equal(button.attributes['aria-pressed'],'false');assert.equal(button.attributes['data-enabled'],'false');
 assert.equal(ui.all(ui.panel.globalReset).filter(n=>n.tag==='input').length,0);
 const before=ui.panel.globalReset.children[0];ui.panel.render();assert.equal(ui.panel.globalReset.children[0],before);
 let finish;ui.setRequest(()=>new Promise(resolve=>finish=resolve));const pending=button.onclick();await button.onclick();
 assert.equal(ui.requests.length,1);assert.equal(ui.all(ui.panel.globalReset).find(n=>n.tag==='button').disabled,true);
 finish({...ui.value(),reminders:{globalReset:true,globalResetSignal:true}});await pending;
 assert.equal(ui.requests.at(-1).action,'reminders');assert.equal(ui.requests.at(-1).preferences.globalReset,true);
 assert.equal(ui.requests.at(-1).preferences.globalResetSignal,true);
 const enabled=ui.all(ui.panel.globalReset).find(n=>n.tag==='button');assert.equal(enabled.attributes['data-enabled'],'true');assert.equal(enabled.attributes['aria-pressed'],'true');assert.equal(enabled.attributes['aria-label'],'关闭重置动态提醒');
 ui.setRequest(async()=>{throw Error('保存失败');});await enabled.onclick();
 assert.equal(ui.all(ui.panel.globalReset).find(n=>n.tag==='button').attributes['data-enabled'],'true');assert.match(ui.panel.error.textContent,/保存失败/);
 ui.setRequest(async()=>({...ui.value(),reminders:{globalReset:false,globalResetSignal:false}}));await ui.all(ui.panel.globalReset).find(n=>n.tag==='button').onclick();
 assert.equal(ui.requests.at(-1).preferences.globalReset,false);assert.equal(ui.requests.at(-1).preferences.globalResetSignal,false);
 assert.equal(ui.all(ui.panel.globalReset).find(n=>n.tag==='button').attributes['data-enabled'],'false');
 ui.panel.clear();assert.equal(ui.panel.globalReset.hidden,true);assert.equal(ui.panel.globalReset.children.length,0);
});

test('global outlook and relative dates translate without translating account identity',()=>{
 const ui=fixture(),now=ui.now()/1000;
 ui.panel.accept({...ui.value(),globalReset:{state:'ready',updatedAt:now,lastResetAt:now-60}});ui.language();
 assert.match(ui.text(),/Global reset confirmed · Today /);assert.match(ui.text(),/Source: codex-reset.com/);assert.match(ui.text(),/API <script>/);
});

test('native login card survives an empty saved list without enrollment or switching actions',async()=>{
 const ui=fixture(),login={id:'current-login',kind:'chatgpt',name:'ChatGPT',email:'current@example.test',transient:true,details:{usage:{status:'ready',checkedAt:ui.now()/1000,limits:[],resetCredits:{availableCount:2}}}};
 ui.panel.onReset=()=>{};
 ui.setValue({...ui.value(),accounts:[],loginAccount:login,current:{status:'ready',kind:'api',name:'API · custom'}});
 await ui.panel.refresh();
 assert.match(ui.text(),/current@example.test/);assert.match(ui.text(),/已登录/);assert.match(ui.text(),/2 张可用/);
 assert.equal(ui.all().filter(n=>n.tag==='section'&&n.className==='account-bucket').length,1);
 assert.equal(ui.all().some(n=>['切换','删除','修改','查看可用模型'].includes(n.textContent)),false);
 assert.ok(ui.all().find(n=>n.tag==='button'&&n.textContent==='查看'));
 ui.panel.accept({...ui.value(),accounts:[{...login,transient:false}],loginAccount:{...login,transient:false}});
 assert.equal(ui.all().filter(n=>n.tag==='section'&&n.className==='account-bucket').length,1);
});

test('web exposes saved account selection without enrollment or credential inputs',async()=>{
 const ui=fixture();await ui.panel.refresh();assert.equal(ui.all().some(n=>n.tag==='input'&&n.type!=='checkbox'),false);assert.match(ui.text(),/API <script>/);assert.equal(ui.all().some(n=>n.tag==='script'),false);
 ui.confirm(false);await ui.panel.choose(ui.value().accounts[0]);assert.equal(ui.requests.length,0);
 ui.confirm(true);await ui.panel.choose(ui.value().accounts[0]);assert.deepEqual(Object.keys(ui.requests[0]).sort(),['action','confirmed','id','requestId','tasksConfirmed']);
 assert.equal(ui.requests[0].tasksConfirmed,true);assert.match(ui.prompts[0],/未在网页显示/);
});

for(const desktop of [false,true])test(`${desktop?'desktop':'web'} merges imported login and keeps other official accounts and API cards separate`,async()=>{
 const ui=fixture(desktop),usage={status:'ready',checkedAt:ui.now()/1000,limits:[{name:'Codex',windows:[{remainingPercent:75,windowDurationMins:300}]}],resetCredits:{availableCount:2}};
 const login={id:'one',kind:'chatgpt',name:'My login',email:'same@example.test',accountKey:'a'.repeat(64),details:{usage}};
 const other={id:'two',kind:'chatgpt',name:'Other workspace',email:login.email,details:{usage}};
 const api={id:'api',kind:'api',name:'Current API',baseUrl:'https://fixture.test/v1',model:'routed'};
 ui.panel.onReset=()=>{};
 ui.panel.accept({...ui.value(),accounts:[other,api,login],loginAccount:login,activeId:api.id,current:{kind:'api',id:api.id,status:'ready',name:api.name}});
 const cards=ui.panel.rows.children.filter(n=>n.className==='account-bucket');
 assert.equal(cards.length,3);assert.equal(ui.all(cards[0]).find(n=>n.tag==='h3').textContent,login.name);
 assert.equal(ui.all().filter(n=>n.textContent==='已登录').length,1);
 assert.equal(ui.all().filter(n=>n.textContent==='使用中').length,1);
 assert.equal(ui.all().filter(n=>n.tag==='progress').length,2);
 assert.equal(ui.all().filter(n=>n.tag==='button'&&n.textContent==='查看').length,2);
 const transient={...login,id:'current-login',transient:true};
 ui.panel.accept({...ui.value(),accounts:[],activeId:null,loginAccount:transient});
 assert.equal(ui.panel.rows.children.filter(n=>n.className==='account-bucket').length,1);
 assert.equal(ui.all().some(n=>['切换','使用中'].includes(n.textContent)),false);
});
test('passwordless web disables switching and explains the desktop alternative',async()=>{
 const ui=fixture();ui.setValue({...ui.value(),canSwitch:false});await ui.panel.refresh();
 assert.equal(ui.all().find(n=>n.textContent==='切换').disabled,true);assert.match(ui.text(),/免密访问不能切换/);
});
test('switch phases disable repeat actions and recovery only exists on desktop',async()=>{
 const ui=fixture(true);ui.setValue({...ui.value(),switch:{phase:'verifying'}});await ui.panel.refresh();
 assert.equal(ui.all().find(n=>n.textContent==='切换').disabled,true);assert.equal(ui.panel.submit.disabled,true);assert.equal(ui.panel.recover.disabled,true);
 ui.setValue({...ui.value(),switch:{phase:'interrupted'}});await ui.panel.refresh();assert.equal(ui.panel.recover.disabled,false);
});
test('desktop masks API keys and never prepopulates a saved key',async()=>{
 const ui=fixture(true);await ui.panel.refresh();assert.equal(ui.panel.key.input.type,'password');
 ui.all().find(n=>n.textContent==='修改').onclick();assert.equal(ui.panel.key.input.value,'');assert.equal(ui.panel.url.input.value,'https://example.test/v1');
});
test('official login is opened by trusted desktop IPC, not arbitrary browser navigation',async()=>{
 const ui=fixture(true);ui.setValue({...ui.value(),enrollment:{phase:'waiting',authUrl:'https://auth.openai.com/authorize?fixture=1'}});await ui.panel.refresh();
 assert.equal(ui.all().some(n=>n.tag==='a'),false);await ui.all().find(n=>n.textContent==='打开官方登录').onclick();assert.equal(ui.requests[0].action,'openLogin');
});
test('desktop renames through an inline form without unsupported native prompts',async()=>{
 const ui=fixture(true);await ui.panel.refresh();ui.all().find(n=>n.textContent==='重命名').onclick();
 assert.equal(ui.panel.renameForm.hidden,false);assert.equal(ui.panel.renameName.input.value,'API <script>');
 ui.panel.renameName.input.value='New name';await ui.panel.renameForm.onsubmit({preventDefault(){}});
 assert.equal(ui.requests[0].action,'rename');assert.equal(ui.requests[0].name,'New name');assert.equal(ui.panel.renameForm.hidden,true);
});
test('late response cannot refill the account selector after logout',async()=>{
 const ui=fixture();let finish;ui.setRead(()=>new Promise(r=>finish=r));const p=ui.panel.refresh();ui.panel.clear();finish(ui.value());await p;assert.equal(ui.panel.value,null);assert.equal(ui.panel.rows.children.length,0);
});
test('English dynamic states retain account names',async()=>{
 const ui=fixture();await ui.panel.refresh();ui.language();assert.match(ui.text(),/Choose a saved account/);assert.match(ui.text(),/API <script>/);
});
test('a read started before an account mutation cannot resurrect a deleted row',async()=>{
 const ui=fixture(true);await ui.panel.refresh();let finish;
 ui.setRead(()=>new Promise(r=>finish=r));const stale=ui.panel.refresh();
 ui.setRequest(async()=>({...ui.value(),accounts:[]}));await ui.panel.perform({action:'delete',id:'a'});
 finish(ui.value());await stale;assert.equal(ui.panel.value.accounts.length,0);
});
test('desktop scanner imports official logins without displaying credentials',async()=>{
 const ui=fixture(true);ui.setValue({...ui.value(),discovery:{source:'/fixture/home',candidates:[{id:'official',kind:'chatgpt',name:'Local login',canImport:true}]}});await ui.panel.refresh();
 await ui.all().find(n=>n.textContent==='导入账号').onclick();assert.equal(ui.requests[0].action,'import');assert.equal(ui.requests[0].candidateId,'official');
 assert.equal(ui.requests[0].apiKey,undefined);
});
test('scanned APIs fetch models using opaque candidate IDs and import the selected model',async()=>{
 const ui=fixture(true);await ui.panel.refresh();ui.panel.importCandidate({id:'candidate',kind:'api',name:'Local API',baseUrl:'https://example.test/v1',model:''});
 assert.equal(ui.panel.key.input.disabled,true);assert.equal(ui.panel.key.input.value,'');
 ui.setRequest(async()=>({...ui.value(),models:['model-one','model-two']}));await ui.panel.modelsButton.onclick();
 assert.equal(ui.requests[0].candidateId,'candidate');assert.equal(ui.requests[0].apiKey,undefined);assert.equal(ui.panel.modelOptions.hidden,false);
 ui.panel.modelOptions.value='model-two';ui.panel.modelOptions.onchange();await ui.panel.form.onsubmit({preventDefault(){}});
 assert.equal(ui.requests[1].action,'import');assert.equal(ui.requests[1].model,'model-two');assert.equal(ui.requests[1].candidateId,'candidate');
});
test('a delayed model response does not refill a changed API form',async()=>{
 const ui=fixture(true);await ui.panel.refresh();ui.panel.kind.value='api';ui.panel.url.input.value='https://first.test/v1';ui.panel.key.input.value='fixture-key';
 let finish;ui.setRequest(()=>new Promise(r=>finish=r));const pending=ui.panel.modelsButton.onclick();ui.panel.url.input.value='https://second.test/v1';ui.panel.url.input.oninput();
 finish({...ui.value(),models:['old-provider-model']});await pending;assert.equal(ui.panel.modelOptions.hidden,true);
});
test('model-fetch errors remain visible after status refresh and manual entry stays available',async()=>{
 const ui=fixture(true);await ui.panel.refresh();ui.panel.kind.value='api';ui.panel.kind.onchange();ui.setRequest(async()=>{throw Error('上游拒绝访问，请检查 API Key 和模型列表权限');});
 await ui.panel.modelsButton.onclick();await ui.panel.refresh();assert.match(ui.panel.error.textContent,/上游拒绝/);assert.equal(ui.panel.model.wrap.hidden,false);
});
test('LAN HTTP switching works without crypto.randomUUID and reports failures',async()=>{
 const ui=fixture();await ui.panel.refresh();ui.noRandomUUID();
 ui.setRequest(async()=>{throw Error("Error invoking remote method 'bridge:accounts': Error: task blocked");});
 await ui.panel.choose(ui.value().accounts[0]);assert.equal(ui.requests.length,1);assert.match(ui.requests[0].requestId,/^[0-9a-f-]{36}$/);
 assert.equal(ui.panel.error.textContent,'task blocked');
});
test('actual current account is named and cannot be selected again',async()=>{
 const ui=fixture();ui.setValue({...ui.value(),activeId:'a',current:{status:'ready',id:'a',kind:'api',name:'Imported current'}});await ui.panel.refresh();
 assert.match(ui.panel.current.textContent,/Imported current/);assert.equal(ui.all().find(n=>n.textContent==='使用中').disabled,true);
});
test('official rows show compact quota and reset count, while API rows only offer models',async()=>{
 const ui=fixture();ui.setValue({...ui.value(),accounts:[...ui.value().accounts,{id:'official',kind:'chatgpt',name:'Official',details:{usage:{status:'ready',checkedAt:Date.now()/1000,limits:[{name:'Codex',windows:[{remainingPercent:75,windowDurationMins:300}]}],resetCredits:{availableCount:2}},models:{status:'ready',models:[{id:'model-1',name:'Model one'}]}}}]});
 await ui.panel.refresh();assert.match(ui.text(),/剩余 75%/);assert.match(ui.text(),/重置卡 · 2/);assert.equal(ui.all().filter(n=>n.tag==='progress').length,1);
 assert.equal(ui.all().filter(n=>n.textContent==='查看可用模型').length,2);
 ui.panel.expandedModels.add('official');ui.panel.render();assert.match(ui.text(),/Model one · model-1/);
});
test('blockers show the concrete running or pending chat instead of a generic warning',async()=>{
 const ui=fixture();ui.setValue({...ui.value(),blockers:[{reason:'approval',title:'Review fixture'}]});await ui.panel.refresh();assert.match(ui.text(),/Review fixture/);
});

for(const desktop of [false,true])test(`${desktop?'desktop':'web'} automatically loads initial quota and restarts five minutes after a manual query`,async()=>{
 const ui=fixture(desktop),official={id:'official',kind:'chatgpt',name:'Official'};
 ui.setValue({...ui.value(),activeId:'official',accounts:[...ui.value().accounts,official]});
 ui.setRequest(async()=>{official.details={usage:{status:'ready',checkedAt:ui.now()/1000,limits:[]}};return structuredClone(ui.value());});
 ui.root.closest=selector=>selector.includes('[hidden]')?{}:null;
 await ui.panel.refresh();assert.equal(ui.requests.length,0);
 ui.root.closest=()=>null;await ui.panel.refresh();assert.equal(ui.requests.length,1);
 assert.equal(ui.requests[0].section,'usage');assert.equal(ui.requests[0].id,'official');
 assert.equal(ui.all(ui.panel.rows.children[0]).find(n=>n.tag==='h3').textContent,'Official');
 assert.equal(ui.all(ui.panel.rows.children[0]).find(n=>n.textContent==='使用中').disabled,true);
 ui.advance(120000);await ui.panel.refresh();assert.equal(ui.requests.length,1);
 await ui.all().find(n=>n.attributes['aria-label']==='查看剩余额度').onclick();assert.equal(ui.requests.length,2);assert.equal(ui.requests[1].refresh,true);
 ui.advance(180000);await ui.panel.refresh();assert.equal(ui.requests.length,2,'old five-minute deadline is cancelled');
 ui.advance(119000);await ui.panel.refresh();assert.equal(ui.requests.length,2);
 ui.advance(2000);ui.hidden(true);await ui.panel.refresh();assert.equal(ui.requests.length,2);
 ui.hidden(false);await ui.panel.refresh();assert.equal(ui.requests.length,3);
 await ui.panel.refresh();assert.equal(ui.requests.length,3,'status polling does not repeat the quota query');
});
test('quota and reset count precede all account actions',async()=>{
 const ui=fixture();ui.setValue({...ui.value(),accounts:[{id:'official',kind:'chatgpt',name:'Official',details:{usage:{status:'ready',checkedAt:Date.now()/1000,limits:[],resetCredits:{availableCount:2}}}}]});
 await ui.panel.refresh();const nodes=ui.all(ui.panel.rows.children[0]);
 assert.ok(nodes.findIndex(n=>n.textContent==='重置卡 · 2 张可用')<nodes.findIndex(n=>n.textContent==='切换'));
 const refresh=nodes.find(n=>n.attributes['aria-label']==='查看剩余额度');assert.equal(refresh.textContent,'');assert.match(refresh.innerHTML,/<svg[^>]*viewBox="0 0 24 24"/);assert.equal(refresh.parent.className,'accounts-usage-controls');assert.equal(refresh.parent.parent,nodes.find(n=>n.className==='accounts-actions'));assert.ok(!nodes.some(n=>n.textContent==='刷新额度'));
});

for(const desktop of [false,true])test(`${desktop?'desktop':'web'} groups reset count, next usable expiry and the selected account action`,()=>{
 const ui=fixture(desktop);ui.panel.onReset=()=>{};let chosen;ui.panel.openReset=row=>chosen=row.id;
 const now=ui.now()/1000,expiry=now+86400,card=(id,expiresAt,extra={})=>({id,expiresAt,status:'available',resetType:'codexRateLimits',...extra});
 const official={id:'official',kind:'chatgpt',name:'Official',email:'official@test',details:{usage:{status:'ready',updatedAt:now,limits:[],subscription:{periodEndsAt:now+86400,source:'online'},resetCredits:{availableCount:2,credits:[card('later',expiry+86400),card('expired',now-1),card('other',now+10,{resetType:'other'}),card('used',now+20,{status:'used'}),card('next',expiry)]}}}};
 ui.panel.accept({...ui.value(),accounts:[...ui.value().accounts,official]});
 const nodes=ui.all(ui.panel.rows.children[1]),group=nodes.find(n=>n.className==='accounts-reset-summary'),grouped=ui.all(group);
 assert.match(grouped.map(n=>n.textContent).join('\n'),/重置卡 · 2 张可用/);
 const time=grouped.find(n=>n.tag==='time');assert.equal(time.dateTime,new Date(expiry*1000).toISOString());assert.equal(time.title,new Date(expiry*1000).toLocaleString('zh-CN'));
 const use=grouped.find(n=>n.tag==='button');assert.equal(use.parent,group);use.onclick();assert.equal(chosen,'official');assert.equal(ui.requests.length,0);
 assert.equal(ui.all(ui.panel.rows.children[0]).some(n=>n.className==='accounts-reset-summary'),false);
 const note=nodes.find(n=>n.className==='account-muted account-subscription-note'),info=nodes.find(n=>n.attributes['aria-label']==='订阅日期说明');assert.equal(note.hidden,true);info.onclick();assert.equal(note.hidden,false);assert.equal(info.attributes['aria-expanded'],'true');
 assert.equal(info.parent.parent.className,'accounts-name');assert.equal(info.textContent,'订阅周期至 '+new Date(expiry*1000).toLocaleDateString('zh-CN',{month:'short',day:'numeric'}));assert.ok(info.title.includes(new Date(expiry*1000).toLocaleString('zh-CN')));assert.ok(!nodes.some(n=>n.textContent==='ⓘ'));
 ui.panel.render();assert.equal(ui.all().find(n=>n.className==='account-muted account-subscription-note').hidden,false);
 const updated=nodes.find(n=>n.className==='account-updated account-muted'),refresh=nodes.find(n=>n.attributes['aria-label']==='查看剩余额度');assert.equal(updated.textContent,new Date(now*1000).toLocaleString('zh-CN',{hour:'2-digit',minute:'2-digit'})+' 更新');assert.equal(updated.parent,refresh.parent);assert.equal(updated.parent.children[0],updated);assert.equal(updated.title,new Date(now*1000).toLocaleString('zh-CN'));
 ui.language();assert.match(ui.text(),/Usage resets · 2 available/);assert.match(ui.text(),/Subscription period ends:/);
});

test('official credits retain zero and unlimited balances and omit missing values',()=>{
 const ui=fixture();ui.panel.accept({...ui.value(),accounts:[{id:'official',kind:'chatgpt',name:'Official',details:{usage:{status:'ready',limits:[{name:'Missing',credits:{balance:null}},{name:'Empty',credits:{balance:' '}},{name:'Zero',credits:{balance:0}},{name:'Paid',credits:{balance:'12.5'}},{name:'Unlimited',credits:{unlimited:true}}]}}}]});
 assert.doesNotMatch(ui.text(),/Missing · Credits:|Empty · Credits:/);assert.match(ui.text(),/Zero · Credits: 0/);assert.match(ui.text(),/Paid · Credits: 12.5/);assert.match(ui.text(),/Unlimited · Credits: 不限量/);
});
test('viewed quotas refresh at five minutes only while the account page is visible',async()=>{
 const ui=fixture();const official={id:'official',kind:'chatgpt',name:'Official',details:{usage:{status:'ready',checkedAt:Date.now()/1000-299,limits:[]}}};
 ui.setValue({...ui.value(),accounts:[official]});await ui.panel.refresh();assert.equal(ui.requests.length,0);
 official.details.usage.checkedAt=Date.now()/1000-301;
 ui.root.closest=selector=>selector==='dialog'?{open:false}:null;await ui.panel.refresh();assert.equal(ui.requests.length,0);
 ui.root.closest=selector=>selector.includes('[hidden]')?{}:null;await ui.panel.refresh();assert.equal(ui.requests.length,0);
 ui.root.closest=()=>null;
 ui.setRequest(async()=>{official.details.usage.checkedAt=Date.now()/1000;return structuredClone(ui.value());});
 await ui.panel.refresh();assert.equal(ui.requests.length,1);assert.equal(ui.requests[0].section,'usage');assert.equal(ui.requests[0].refresh,undefined);
 await ui.all().find(n=>n.attributes['aria-label']==='查看剩余额度').onclick();assert.equal(ui.requests.length,2);assert.equal(ui.requests[1].refresh,true);
});


test('desktop and web can ignore only the selected unknown submission',async()=>{
 for(const desktop of [false,true]){
  const ui=fixture(desktop);ui.setValue({...ui.value(),blockers:[{reason:'unknown',title:'Chat',id:'thread',submissionId:'message',host:'local'},{reason:'running',title:'Busy',id:'busy'}]});
  await ui.panel.refresh();assert.equal(ui.all().filter(n=>n.textContent==='忽略').length,1);
  ui.confirm(false);await ui.all().find(n=>n.textContent==='忽略').onclick();assert.equal(ui.requests.length,0);
  ui.confirm(true);await ui.all().find(n=>n.textContent==='忽略').onclick();
  assert.deepEqual(JSON.parse(JSON.stringify(ui.requests[0])),{action:'ignoreSubmission',threadId:'thread',submissionId:'message',host:'local'});
 }
});

test('loading and failed refresh retain percentages, cards and the last successful time',()=>{
 const ui=fixture();const usage={status:'ready',updatedAt:1900000000,limits:[{name:'Codex',windows:[{remainingPercent:75,windowDurationMins:300,resetsAt:2000000000}]}],resetCredits:{availableCount:2}};
 for(const status of ['ready','loading','error']){ui.panel.accept({...ui.value(),accounts:[{id:'official',kind:'chatgpt',name:'Official',details:{usage:{...usage,status,error:status==='error'?'查询失败':undefined}}}]});
 assert.match(ui.text(),/剩余 75%/);assert.match(ui.text(),/重置卡 · 2/);assert.match(ui.all().find(n=>n.className==='account-updated account-muted').textContent,/更新/);assert.match(ui.text(),/后恢复|等待刷新/);
 if(status==='loading')assert.match(ui.text(),/查询中/);if(status==='error')assert.match(ui.text(),/保留上次结果/);}
 ui.panel.accept({...ui.value(),accounts:[{id:'other',kind:'chatgpt',name:'Different',details:{usage:{status:'loading'}}}]});assert.doesNotMatch(ui.text(),/剩余 75%|重置卡 · 2/);
});
test('current-account card has a direct reset entry and API cards have no subscription quota',()=>{
 const ui=fixture();let chosen;ui.panel.onReset=()=>{};ui.panel.openReset=row=>chosen=row.id;
 ui.panel.accept({...ui.value(),activeId:'official',accounts:[{id:'official',kind:'chatgpt',name:'Official',details:{usage:{status:'ready',limits:[],resetCredits:{availableCount:2}}}}]});
 ui.all().find(n=>n.tag==='button'&&n.textContent==='查看'&&!n.hidden).onclick();assert.equal(chosen,'official');
});

test('active official account exposes reset entry before quota details load',()=>{
 const ui=fixture();let chosen;ui.panel.onReset=()=>{};ui.panel.openReset=row=>chosen=row.id;
 ui.panel.accept({...ui.value(),activeId:'official',current:{kind:'chatgpt',status:'ready',id:'official'},accounts:[{id:'official',kind:'chatgpt',name:'Official'}]});
 const reset=ui.all().find(n=>n.tag==='button'&&n.textContent==='查看'&&!n.hidden);assert.ok(reset);reset.onclick();assert.equal(chosen,'official');
});


test('both saved-account views treat stale subscription claims as unknown',()=>{
 for(const desktop of [false,true]){
  const ui=fixture(desktop);
  const accounts=[1790147934,1788250708].map((periodEndsAt,i)=>({id:'official-'+i,kind:'chatgpt',name:'Account '+i,details:{usage:{status:'ready',checkedAt:ui.now()/1000,updatedAt:ui.now()/1000,limits:[],subscription:{periodEndsAt,source:'login'}}}}));
  ui.panel.accept({...ui.value(),accounts});
  assert.equal(ui.all().filter(n=>n.textContent==='订阅有效期暂未确认').length,2);
  assert.doesNotMatch(ui.text(),/当前订阅周期|已到记录日期|后结束/);
  accounts[0].details.usage.subscription.periodEndsAt=ui.now()/1000+86400;ui.panel.render();
  assert.match(ui.text(),/登录记录中的订阅日期（非实时）：/);assert.match(ui.text(),/请以 ChatGPT 订阅页面为准/);
  ui.language();assert.match(ui.text(),/Subscription date in login record \(not live\):/);
  ui.advance(86401000);ui.panel.render();assert.equal(ui.all().filter(n=>n.textContent==='Subscription end date is unconfirmed').length,2);
 }
});

test('every saved official account opens its own reset dialog without switching',async()=>{
 const ui=fixture();ui.panel.onReset=()=>{};
 const saved={...ui.value(),activeId:'api',current:{kind:'api',status:'ready'},accounts:[...ui.value().accounts,{id:'one',kind:'chatgpt',name:'One'},{id:'two',kind:'chatgpt',name:'Two'}]};
 ui.panel.accept(saved);
 assert.equal(ui.all().filter(n=>n.textContent==='查看').length,2);
 ui.setRequest(async value=>value.action==='account'?{visible:true,email:value.id+'@test',accountKey:'a'.repeat(64),limits:[],canReset:true,resetCredits:{availableCount:0,credits:[]}}:saved);
 await ui.panel.openReset(saved.accounts[2]);
 assert.equal(ui.requests[0].action,'account');assert.equal(ui.requests[0].id,'two');assert.equal(ui.requests[0].operation,'read');
 assert.equal(ui.panel.resetPanel.value.email,'two@test');assert.ok(ui.panel.resetDialog.open);
 assert.equal(ui.panel.resetPanel.resetOnly,true);const dialogText=ui.all(ui.panel.resetDialog).map(n=>n.textContent).join('\n');assert.doesNotMatch(dialogText,/订阅|额度|更新于/);
 assert.equal(ui.requests.some(r=>r.action==='switch'),false);
 ui.panel.resetDialog.close();assert.equal(ui.panel.resetPanel,null);
});

for(const desktop of [false,true])test(`${desktop?'desktop':'web'} reset inventory appears immediately and use waits for the account read`,async()=>{
 const ui=fixture(desktop);ui.panel.onReset=()=>{};
 const cards={availableCount:2,credits:[{id:'credit',title:'Cached reset',status:'available',resetType:'codexRateLimits',expiresAt:null}]};
 const row={id:'official',kind:'chatgpt',name:'Official',email:'saved@test',details:{usage:{status:'ready',resetCredits:cards}}};
 ui.panel.accept({...ui.value(),accounts:[row]});let resolveRead;
 ui.setRequest(()=>new Promise(resolve=>resolveRead=resolve));const opening=ui.panel.openReset(row),panel=ui.panel.resetPanel;
 const text=()=>ui.all(ui.panel.resetDialog).map(n=>n.textContent).join('\n');
 assert.match(text(),/Cached reset/);assert.match(text(),/可用数量：2/);assert.match(text(),/查询中/);assert.doesNotMatch(text(),/此账号暂不可使用/);
 assert.equal(panel.loading,true);assert.equal(panel.canRedeem('credit'),false);await panel.redeem('credit');assert.equal(ui.requests.length,1);assert.equal(ui.requests[0].refresh,false);
 resolveRead({visible:true,email:'saved@test',accountKey:'a'.repeat(64),limits:[],canReset:true,resetCredits:cards});await opening;
 assert.equal(panel.loading,false);assert.equal(panel.canRedeem('credit'),true);assert.doesNotMatch(text(),/查询中/);
 ui.panel.resetDialog.close();
});

test('first reset load shows progress and a closed dialog cannot be repopulated by a late read',async()=>{
 const ui=fixture(),row={id:'official',kind:'chatgpt',name:'Official'};ui.panel.onReset=()=>{};ui.panel.accept({...ui.value(),accounts:[row]});
 let resolveRead;ui.setRequest(()=>new Promise(resolve=>resolveRead=resolve));const opening=ui.panel.openReset(row),panel=ui.panel.resetPanel;
 const nodes=ui.all(ui.panel.resetDialog);assert.ok(nodes.some(n=>n.textContent==='查询中…'&&n.attributes.role==='status'));
 ui.panel.resetDialog.close();resolveRead({visible:true,email:'saved@test',accountKey:'a'.repeat(64),limits:[],canReset:true,resetCredits:{availableCount:0,credits:[]}});await opening;
 assert.equal(ui.panel.resetPanel,null);assert.equal(panel.value,null);assert.equal(panel.root.children.length,0);
});

test('account cards keep only account management and the reset shortcut',async()=>{
 const ui=fixture();await ui.panel.refresh();assert.doesNotMatch(ui.text(),/提醒与 Codex Desktop 更新|Codex Desktop 新版本提醒|查看 Codex 新版本/);
});

for(const desktop of [false,true])test(`${desktop?'desktop':'web'} hides successful switch status and preserves progress and errors`,async()=>{
 const ui=fixture(desktop);
 for(const phase of ['complete','restored']){
  ui.setValue({...ui.value(),switch:{phase}});await ui.panel.refresh();assert.equal(ui.panel.status.hidden,true);
  ui.setValue({...ui.value(),switch:{phase,error:'Review required'}});await ui.panel.refresh();assert.equal(ui.panel.status.hidden,false);assert.match(ui.panel.status.textContent,/Review required/);
 }
 for(const phase of ['preparing','verifying','failed','interrupted']){
  ui.setValue({...ui.value(),switch:{phase}});await ui.panel.refresh();assert.equal(ui.panel.status.hidden,false);
 }
});

test('an initial account read failure retries promptly and recovers without reopening the page',async()=>{
 const ui=fixture();ui.setRead(async()=>{throw Error('Offline');});await ui.panel.refresh();
 assert.equal(ui.panel.value,null);const timer=ui.timers.get(ui.panel.timer);assert.equal(timer.delay,10000);
 ui.setRead(async()=>ui.value());await timer.fn();
 assert.ok(ui.panel.value);assert.equal(ui.panel.readFailed,false);assert.equal(ui.panel.error.textContent,'');
});
