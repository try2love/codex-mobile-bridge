'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
function fixture(){
  function node(tag){return {tag,textContent:'',value:'',hidden:false,disabled:false,children:[],dataset:{},attributes:{},classList:{toggle(){}},append(...children){this.children.push(...children);},replaceChildren(...children){this.children=children;},setAttribute(k,v){this.attributes[k]=v;}};}
  const nodes=Object.fromEntries(['client-overview','client-scan-status','refresh-clients','clients-page'].map(id=>[id,node('div')]));
  const input=node('input'),workspace=node('pre'),claudeHome=node('pre'),claudeData=node('div'),statuses={deepseek:node('p'),claude:node('p')},choose=node('button'),scan=node('button');scan.dataset.desktopAction='scan';claudeData.hidden=true;for(const status of Object.values(statuses))status.dataset.i18n='正在检查接入…';
  const root={hidden:false,querySelector(selector){if(selector.includes('.client-auto-setup'))return null;if(selector==='input')return input;if(selector==='[data-desktop-choose]')return choose;if(selector==='[data-claude-workspace]')return workspace;if(selector==='[data-claude-home]')return claudeHome;if(selector==='[data-claude-data]')return claudeData;return statuses[selector.includes('deepseek')?'deepseek':'claude'];},querySelectorAll(selector){if(selector==='[data-desktop-status]')return Object.values(statuses);if(selector.includes('data-desktop-action'))return [scan];return [];}};
  const calls=[],errors=[],prompts=[];let confirmed=true,handler=async body=>body.action==='status'?{backends:{},deepseekHome:'/fixture'}:{clients:[]};
  const listeners={},timers=[];
  const context=vm.createContext({CustomEvent:class{constructor(type){this.type=type;}},document:{documentElement:{},getElementById:id=>nodes[id],createElement:node,querySelector:()=>null,querySelectorAll:selector=>selector==='[data-i18n]'?Object.values(statuses):[],hidden:false,addEventListener(type,fn){listeners[type]=fn;},dispatchEvent(event){listeners[event.type]?.(event);}},setInterval(fn){timers.push(fn);},localStorage:{getItem(){return null;},setItem(){}},window:{confirm:message=>{prompts.push(message);return confirmed;},GatewayLayout:{selectClient(){}}}});
  for(const file of ['web/i18n.js','desktop/desktop-sessions.js'])vm.runInContext(fs.readFileSync(path.join(__dirname,'..',file),'utf8'),context);
  const Class=vm.runInContext('DesktopConnectionsPanel',context),panel=new Class({root,api:{desktopSessions:body=>{calls.push(body);return handler(body);}},feedback:message=>errors.push(message)});
  const all=n=>[n,...n.children.flatMap(all)];
  return {panel,nodes,calls,errors,scan,prompts,statuses,workspace,claudeHome,claudeData,poll:async()=>{timers[0]();await Promise.resolve();await Promise.resolve();},apply:()=>vm.runInContext('BridgeI18n.apply()',context),confirm:value=>confirmed=value,setHandler:fn=>handler=fn,all:()=>all(nodes['client-overview']),text:()=>all(nodes['client-overview']).map(n=>n.textContent).join('\n'),english:()=>vm.runInContext("BridgeI18n.setLanguage('en')",context)};
}
test('scan discovers clients without connecting apps and ignores duplicate scans',async()=>{
  const ui=fixture();let finish;
  ui.setHandler(body=>body.action==='scan'?new Promise(resolve=>finish=resolve):Promise.resolve(body.action==='status'?{backends:{}}:{clients:[]}));
  const pending=ui.panel.scan();await ui.panel.scan();assert.equal(ui.calls.length,1);assert.equal(ui.calls[0].action,'scan');assert.equal(ui.calls[0].setup,false);assert.ok(ui.nodes['refresh-clients'].disabled);
  finish({clients:[]});await pending;assert.equal(ui.nodes['refresh-clients'].disabled,false);assert.deepEqual(ui.calls.map(c=>c.action),['scan','clients','status']);
});
test('Claude unknown-state quit is a separate explicitly confirmed desktop action',async()=>{
  const ui=fixture(),client={id:'claude',name:'Claude',installed:true,configured:true,connected:false,enabled:true,running:true,canConfirmQuit:true,reason:'连接已断开'};
  ui.panel.clients=[client];ui.panel.renderClients();
  ui.setHandler(async body=>body.action==='claude-quit-preview'?{token:'fixture-preview',canQuit:true,requiresUnknownConfirmation:true}:body.action==='status'?{backends:{}}:{clients:[client]});
  ui.confirm(false);await ui.all().find(n=>n.textContent==='退出 Claude…').onclick();
  assert.ok(ui.prompts[0].includes('可能中断'));assert.equal(ui.calls.some(c=>c.action==='claude-quit-confirm'),false);assert.equal(client.enabled,true);
  ui.confirm(true);await ui.all().find(n=>n.textContent==='退出 Claude…').onclick();
  const value=ui.calls.find(c=>c.action==='claude-quit-confirm');assert.equal(value.token,'fixture-preview');assert.equal(value.confirmed,true);assert.equal(value.acknowledgeUnknown,true);
  assert.equal(ui.calls.some(c=>['toggle-client','restart-claude','connect-claude'].includes(c.action)),false);
});
test('Claude known busy tasks never receive the unknown-state quit confirmation',async()=>{
  const ui=fixture();ui.panel.clients=[{id:'claude',name:'Claude',installed:true,connected:false,enabled:true,running:true,canConfirmQuit:true}];ui.panel.renderClients();
  ui.setHandler(async body=>body.action==='claude-quit-preview'?{token:'fixture-preview',canQuit:false,busy:true}:body.action==='status'?{backends:{}}:{clients:ui.panel.clients});
  await ui.all().find(n=>n.textContent==='退出 Claude…').onclick();assert.equal(ui.prompts.length,0);assert.equal(ui.calls.some(c=>c.action==='claude-quit-confirm'),false);assert.ok(ui.errors.some(x=>x.includes('任务运行')));
});
test('Claude desktop blockers retain their own labels and allow cancelling pending connection',async()=>{
  for(const [setupStatus,label] of [['needs-screen-saver','等待退出屏保'],['needs-unlock','等待解锁'],['needs-desktop','等待桌面恢复']])for(const running of [true,false]){
    const ui=fixture(),client={id:'claude',name:'Claude',installed:true,configured:true,connected:false,enabled:true,running,setupStatus,reason:'waiting reason'};ui.panel.clients=[client];ui.panel.renderClients();
    assert.match(ui.text(),new RegExp(label));ui.setHandler(async body=>body.action==='status'?{backends:{}}:{clients:[client]});await ui.all().find(n=>n.textContent==='取消连接').onclick();assert.equal(ui.calls[0].action,'cancel-claude');
  }
});
test('installed but unsupported Claude is never shown as connected or switchable; original icons remain',()=>{
  const ui=fixture();ui.english();ui.panel.clients=[{id:'claude',name:'Claude',installed:true,configured:false,connected:false,enabled:false,setupStatus:'unsupported',reason:'此版本 Claude Desktop 限制自动接入，暂时无法连接'}];ui.panel.renderClients();
  assert.match(ui.text(),/Automatic access unavailable/);assert.doesNotMatch(ui.text(),/[\u4e00-\u9fff]/);assert.match(ui.text(),/restricts automatic access/);assert.ok(ui.all().find(n=>n.tag==='input').disabled);assert.equal(ui.all().find(n=>n.tag==='img').src,'../web/client-icons/claude.png');
});
test('an offline read failure preserves established configuration instead of inventing unconfigured rows',async()=>{
  const ui=fixture(),clients=[{id:'codex',name:'Codex',configured:true,connected:false,enabled:true,reason:'接入配置已就绪'}];ui.panel.clients=clients;ui.panel.renderClients();ui.setHandler(async()=>{throw Error('read unavailable');});await ui.panel.refreshClients();
  assert.equal(ui.panel.clients,clients);assert.match(ui.text(),/Codex/);assert.match(ui.nodes['client-scan-status'].textContent,/read unavailable/);
});
test('failed automatic scan reports failure, then can recover through the same rescan control',async()=>{
  const ui=fixture();ui.setHandler(async body=>{if(body.action==='scan')throw Error('setup failed');return body.action==='status'?{backends:{}}:{clients:[]};});await ui.panel.scan(true);
  assert.deepEqual(ui.errors,['setup failed']);assert.equal(ui.nodes['refresh-clients'].disabled,false);assert.match(ui.nodes['client-scan-status'].textContent,/setup failed/);
  ui.setHandler(async body=>body.action==='status'?{backends:{}}:{clients:[]});await ui.nodes['refresh-clients'].onclick();assert.equal(ui.panel.scanError,false);assert.match(ui.nodes['client-scan-status'].textContent,/扫描完成/);
});

test('DSH restart button requires explicit confirmation and never starts the public gateway',async()=>{
  const ui=fixture(),clients=[{id:'deepseek',name:'DSH',installed:true,configured:false,connected:false,enabled:false,setupStatus:'restart-required',reason:'请结束当前任务后重启 Harness 完成接入'}];
  ui.panel.clients=clients;ui.panel.renderClients();ui.confirm(false);await ui.all().find(n=>n.textContent==='重启并接入').onclick();assert.equal(ui.calls.length,0);assert.equal(ui.prompts.length,1);
  ui.confirm(true);ui.setHandler(async body=>body.action==='status'?{backends:{}}:{clients});await ui.all().find(n=>n.textContent==='重启并接入').onclick();assert.equal(ui.calls[0].action,'restart-deepseek');assert.equal(ui.calls.some(c=>c.action==='start'),false);assert.ok(ui.errors.includes('正在等待 Harness 连接'));
});
test('DSH first-open action triggers a follow-up scan and uses no manual directory',async()=>{
  const ui=fixture(),clients=[{id:'deepseek',name:'DSH',installed:true,configured:false,connected:false,enabled:false,setupStatus:'needs-first-launch',reason:'请先打开 Harness 完成首次设置，再重新扫描'}];
  ui.panel.clients=clients;ui.panel.renderClients();ui.setHandler(async body=>body.action==='status'?{backends:{}}:{clients});await ui.all().find(n=>n.textContent==='打开并接入').onclick();
  assert.equal(ui.calls[0].action,'connect-deepseek');assert.equal(ui.calls[0].home,undefined);assert.equal(ui.calls.some(c=>c.action==='scan'&&c.setup===false),true);
});

test('Claude setup exposes native permission and developer confirmation without enabling an unverified connection',async()=>{
  const ui=fixture();ui.english();const client={id:'claude',name:'Claude',installed:true,configured:false,connected:false,enabled:false,setupStatus:'needs-permission',reason:'请在系统设置中允许 Claude 连接组件使用辅助功能，授权后会继续连接'};
  ui.panel.clients=[client];ui.panel.renderClients();
  assert.match(ui.text(),/Permission required/);assert.doesNotMatch(ui.text(),/[\u4e00-\u9fff]/);assert.equal(ui.all().find(n=>n.tag==='input').disabled,true);
  ui.setHandler(async body=>body.action==='status'?{backends:{}}:{clients:[client]});await ui.all().find(n=>n.textContent==='Authorize & connect').onclick();assert.equal(ui.calls[0].action,'connect-claude');
  client.setupStatus='needs-developer-mode';client.reason='请确认 Claude 的开发者模式提示，完成后会继续连接';ui.panel.renderClients();
  assert.match(ui.text(),/Developer mode confirmation required/);assert.doesNotMatch(ui.text(),/[\u4e00-\u9fff]/);
  await ui.all().find(n=>n.textContent==='Cancel connection').onclick();assert.ok(ui.calls.some(c=>c.action==='cancel-claude'));
});

test('Claude restart requires confirmation and connected clients no longer offer setup actions',async()=>{
  const ui=fixture(),client={id:'claude',name:'Claude',installed:true,configured:false,connected:false,enabled:false,setupStatus:'restart-required',reason:'未找到 Claude Console，请在结束当前任务后重启 Claude 再连接'};
  ui.panel.clients=[client];ui.panel.renderClients();ui.confirm(false);await ui.all().find(n=>n.textContent==='重启并接入').onclick();assert.equal(ui.calls.length,0);
  ui.confirm(true);ui.setHandler(async body=>body.action==='status'?{backends:{}}:{clients:[client]});await ui.all().find(n=>n.textContent==='重启并接入').onclick();assert.equal(ui.calls[0].action,'restart-claude');
  Object.assign(client,{configured:true,connected:true,enabled:true,setupStatus:'connected',reason:'桌面连接可用'});ui.panel.renderClients();assert.equal(ui.all().find(n=>n.tag==='input').disabled,false);assert.ok(!ui.all().some(n=>n.tag==='button'&&/连接|接入/.test(n.textContent)));
});

const clientState=reason=>({id:'claude',name:'Claude',installed:true,configured:false,connected:false,enabled:false,setupStatus:'failed',reason});
test('periodic client refresh keeps list and open detail status on the same snapshot',async()=>{
  const ui=fixture();ui.panel.clients=[clientState('正在等待 Claude 连接')];ui.panel.renderClients();await ui.panel.refresh();
  ui.setHandler(async()=>({clients:[clientState('未收到 Claude 连接确认，请检查登录和目录信任后重试')]}));await ui.poll();
  assert.match(ui.text(),/未收到 Claude 连接确认/);assert.equal(ui.statuses.claude.textContent,'未收到 Claude 连接确认，请检查登录和目录信任后重试');
});
test('concurrent status and client reads converge regardless of completion order',async()=>{
  for(const statusFirst of [true,false]){
    const ui=fixture();ui.panel.clients=[clientState('旧连接状态')];ui.panel.renderClients();let status,clients;
    ui.setHandler(body=>new Promise(resolve=>{if(body.action==='status')status=resolve;else clients=resolve;}));
    const readingStatus=ui.panel.refresh(),readingClients=ui.panel.refreshClients();
    if(statusFirst){status({backends:{claude:{connected:false}},claudeWorkspace:'/bridge/claude'});await readingStatus;clients({clients:[clientState('新的连接结果')]});await readingClients;}
    else{clients({clients:[clientState('新的连接结果')]});await readingClients;status({backends:{claude:{connected:false}},claudeWorkspace:'/bridge/claude'});await readingStatus;}
    assert.match(ui.text(),/新的连接结果/);assert.equal(ui.statuses.claude.textContent,'新的连接结果');
  }
});
test('late pre-scan client read cannot replace the scan result',async()=>{
  const ui=fixture(),current=clientState('扫描后的连接结果');let finishOld,clientReads=0;
  ui.setHandler(body=>body.action==='clients'?++clientReads===1?new Promise(resolve=>finishOld=resolve):Promise.resolve({clients:[current]}):Promise.resolve(body.action==='status'?{backends:{}}:{clients:[current]}));
  const reading=ui.panel.refreshClients(),scanning=ui.panel.scan();await Promise.resolve();finishOld({clients:[clientState('扫描前的旧结果')]});await Promise.all([reading,scanning]);
  assert.match(ui.text(),/扫描后的连接结果/);assert.doesNotMatch(ui.text(),/扫描前的旧结果/);assert.equal(ui.statuses.claude.textContent,current.reason);
});
test('changing language preserves the latest detail state through the global translation pass',()=>{
  const ui=fixture();ui.panel.clients=[clientState('未收到 Claude 连接确认，请检查登录和目录信任后重试')];ui.panel.renderClients();ui.english();ui.apply();
  assert.match(ui.statuses.claude.textContent,/No connection confirmation/);assert.doesNotMatch(ui.statuses.claude.textContent,/Checking|[\u4e00-\u9fff]/);
});
test('Claude diagnostics distinguish gateway communication and detected client data directories',async()=>{
  const ui=fixture();ui.setHandler(async()=>({backends:{claude:{connected:false,dataHome:'/actual/Claude'}},claudeWorkspace:'/bridge/desktop-sessions/claude'}));await ui.panel.refresh();
  assert.equal(ui.workspace.textContent,'/bridge/desktop-sessions/claude');assert.equal(ui.claudeHome.textContent,'/actual/Claude');assert.equal(ui.claudeData.hidden,false);
  ui.setHandler(async()=>({backends:{claude:{connected:false}},claudeWorkspace:'/bridge/desktop-sessions/claude'}));await ui.panel.refresh();assert.equal(ui.claudeData.hidden,true);assert.equal(ui.claudeHome.textContent,'');
  const html=fs.readFileSync(path.join(__dirname,'../desktop/index.html'),'utf8');assert.match(html,/data-i18n="网关通信目录"/);assert.match(html,/data-i18n="Claude 数据目录"/);
});
test('older status response cannot restore a stale diagnostic directory',async()=>{
  const ui=fixture(),pending=[];ui.setHandler(()=>new Promise(resolve=>pending.push(resolve)));const old=ui.panel.refresh(),latest=ui.panel.refresh();
  pending[1]({backends:{},claudeWorkspace:'/new/claude'});await latest;pending[0]({backends:{},claudeWorkspace:'/old/claude'});await old;assert.equal(ui.workspace.textContent,'/new/claude');
});
test('process toggle is pending immediately, excludes duplicate operations and rolls back a refused quit',async()=>{
  const ui=fixture(),client={id:'claude',name:'Claude',configured:true,enabled:true,connected:true,reason:'桌面连接可用'};let reject;
  ui.panel.clients=[client];ui.setHandler(body=>body.action==='toggle-client'?new Promise((_,failed)=>reject=failed):Promise.resolve({clients:[client]}));
  const pending=ui.panel.toggleClient('claude',false);
  assert.equal(ui.panel.clients[0].enabled,false);assert.match(ui.text(),/正在退出应用/);assert.ok(ui.all().find(n=>n.tag==='input').disabled);
  await ui.panel.toggleClient('claude',true);await ui.panel.scan();assert.equal(ui.calls.length,1);
  reject(Error('有任务运行或等待确认'));await pending;
  assert.equal(ui.panel.clients[0].enabled,true);assert.equal(ui.panel.busy,false);assert.equal(ui.all().find(n=>n.tag==='input').disabled,false);assert.deepEqual(ui.errors,['有任务运行或等待确认']);
});

test('DSH recovery uses a preview token and explicit acknowledgment for unknown background tasks',async()=>{
 const ui=fixture();ui.english();const clients=[{id:'deepseek',name:'DSH',enabled:true,configured:true,connected:false,running:true,mainRunning:false,backgroundRunning:true,reason:'客户端已配置，可开启桌面应用'}];ui.panel.clients=clients;
 ui.setHandler(async body=>body.action==='deepseek-recovery-preview'?{token:'review-token',canRecover:true,busy:false,requiresUnknownConfirmation:true,guiCount:0,backgroundCount:2}:body.action==='status'?{backends:{}}:{clients});
 await ui.panel.recoverDeepseek(true);const actions=ui.calls.filter(row=>row.action.startsWith('deepseek-recovery'));assert.equal(actions.length,2);assert.equal(actions[0].restart,true);assert.equal(actions[1].token,'review-token');assert.equal(actions[1].acknowledgeUnknown,true);assert.equal(actions[1].confirmed,true);assert.equal(actions[1].pid,undefined);assert.match(ui.prompts[0],/could not be verified/);assert.doesNotMatch(ui.prompts[0],/[\u4e00-\u9fff]/);assert.ok(!ui.errors.includes('DSH 已重新接入'));
});
test('busy DSH tasks prevent recovery and cancelling the preview never quits a client',async()=>{
 for(const busy of [true,false]){
  const ui=fixture();ui.panel.clients=[];ui.confirm(false);ui.setHandler(async body=>body.action==='deepseek-recovery-preview'?{token:'cancel',canRecover:!busy,busy,guiCount:1,backgroundCount:1}:body.action==='status'?{backends:{}}:{clients:[]});
  await ui.panel.recoverDeepseek(false);assert.equal(ui.calls.filter(row=>row.action==='deepseek-recovery-confirm').length,0);assert.equal(ui.prompts.length,busy?0:1);assert.equal(ui.panel.busy,false);
 }
});
test('background-only Harness exposes recovery instead of the legacy restart path',()=>{
 const ui=fixture();ui.panel.clients=[{id:'deepseek',name:'DSH',installed:true,configured:true,enabled:true,connected:true,running:true,mainRunning:false,backgroundRunning:true,setupStatus:'restart-required',reason:'接入配置已就绪'}];ui.panel.renderClients();assert.match(ui.text(),/后台运行，桌面未打开/);assert.ok(ui.all().some(row=>row.tag==='button'&&row.textContent==='恢复并重新接入'));assert.ok(!ui.all().some(row=>row.tag==='button'&&row.textContent==='重启并接入'));
});
