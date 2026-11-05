'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const app=fs.readFileSync(path.join(__dirname,'../web/shell/app.js'),'utf8');
const html=fs.readFileSync(path.join(__dirname,'../web/index.html'),'utf8');
const extract=(start,end)=>app.slice(app.indexOf(start),app.indexOf(end,app.indexOf(start)));
const accessCode=extract('function sessionAccess(access){','function renderList(){');
const sourceFilterCode=extract('function renderSourceFilter(){','function sessionAccess(access){');
const capabilitiesCode=extract('function applyEntryCapabilities(view){','function renderState(view){');
const listCode=extract('function renderList(){','async function loadList(');
const stateCode=extract('function renderState(view){',"$('send-mode').onchange");
const reconnectCode=extract("$('reconnect').onclick=", "document.querySelectorAll('[data-close]')");
const permissionCode=extract("$('permissions-button').onclick=",'const fileTransferSettings=');
const newChatTypeCode=extract("let newChatRequest=0,", "$('new-chat-project').onchange=");
test('CLI creation picker offers existing and custom folders with an accurate action',async()=>{
  const nodes=new Map(),$=id=>{if(!nodes.has(id))nodes.set(id,{value:'',textContent:'',disabled:false,open:true,children:[],addEventListener(){},get options(){return this.children;},replaceChildren(...parts){this.children=parts;this.value=parts[0]?.value||'';},append(option){this.children.push(option);if(!this.value)this.value=option.value;},onchange(){}});return nodes.get(id);};
  const ctx={$,listRows:[],currentId:null,currentHost:'local',newChatProjects:[],newChatEnvironments:[],newChatCanBrowse:false,newChatPreferredHost:'local',newChatEntry:'desktop',preferredWslEntry:'',codexEntries:()=>[],wslEntries:()=>[],newChatUrl:value=>value,independentProject:()=> 'local|independent',api:async()=>({projects:ctx.projects||[],environments:[{key:'cli|local',host:'local',type:'cli',name:'Local CLI'},{key:'desktop|local',host:'local',type:'desktop',name:'Local App'}]}),Option:class{constructor(text,value){this.text=text;this.value=value;}},t:key=>({'创建聊天':'Create chat','创建并打开':'Create and open'}[key]||key)};
  $('new-chat-project').onchange=()=>{$('new-chat-submit').disabled=!$('new-chat-project').value||$('new-chat-project').value==='__directory__';};
  vm.createContext(ctx);require('./fixtures/codex-entry-predicates.cjs')(ctx);vm.runInContext(newChatTypeCode,ctx);
  $('new-chat-type').value='cli';await $('new-chat-type').onchange();
  assert.equal($('new-chat-submit').disabled,true);
  assert.equal($('new-chat-submit').textContent,'Create chat');
  assert.equal($('new-chat-project').children.some(option=>option.value==='__directory__'),true);
  ctx.projects=[{type:'cli',host:'local',key:'cli|local|folder',name:'agent_chat',cwd:'/fixture/agent_chat'}];
  await $('new-chat-type').onchange();
  assert.equal($('new-chat-project').value,'cli|local|folder');
  assert.equal($('new-chat-submit').disabled,false);
  $('new-chat-type').value='desktop';await $('new-chat-type').onchange();
  assert.equal($('new-chat-submit').textContent,'Create and open');
  assert.equal($('new-chat-project').children.some(option=>option.value==='__directory__'),true);
});
function node(){return {dataset:{},setAttribute(){},addEventListener(){},hidden:false,disabled:false,value:'',textContent:'',title:'',children:[],scrollTop:0,classList:{toggle(){}},append(...parts){this.children.push(...parts);},replaceChildren(...parts){this.children=parts;},querySelector(){return node();}};}
function fixture(){
  const nodes=new Map();const $=id=>{if(!nodes.has(id))nodes.set(id,node());return nodes.get(id);};
  const ctx={CliConnectionsPanel:class{},api:async()=>{},contextCompacting:new Set(),uuid:()=>"fixture-id",toast(){},window:{},SessionMenu:class{bind(){}},pinRow:row=>row,$ ,state:null,reconnectFailure:null,listRows:[],availableSources:[],listRenderStamp:'',listComplete:false,listReadFinished:false,listHasMore:false,currentId:'a',currentHost:'local',sending:false,catalogStarted:false,catalogLoads:0,startCatalog(){ctx.catalogLoads++;},selectedSkills:new Set(),invalidSkills:new Set(),renderSkillPills(){},BridgeI18n:{locale:()=> 'en-US'},t:key=>({'此电脑':'This computer','其他来源':'Other source','历史记录':'History','重新连接':'Reconnect','继续会话':'Continue chat'}[key]||key),localStorage:{getItem:()=>null,setItem(){}},dateText:()=> 'today',el:(tag,cls='',value='')=>Object.assign(node(),{tag,cls,textContent:value,dataset:{}}),renderActivity(){},contextUsageControl:{update(){}},fastModeControl:{sync(){}},visibleChat:()=>false,sessionActivity:{read(){}},attachments:{ready:()=>true},workModes:{render(view){ctx.modesView=view;}},workbench:{current:{}},chatTimeline:{refreshActions(){}},renderApprovals(rows){ctx.approvals=rows;},renderQueue(){},approvalStamp:'',chatKey:()=> 'local|a'};
  ctx.chatOpenGeneration=0;ctx.modelCatalogGeneration=0;ctx.setTimeout=fn=>fn();ctx.chatTimeline.abort={signal:{aborted:false}};
  ctx.document={createElement:()=>node(),querySelectorAll:()=>[]};ctx.BridgeI18n.language=()=> 'en-US';ctx.BridgeI18n.t=ctx.t;
  vm.createContext(ctx);vm.runInContext(fs.readFileSync(path.join(__dirname,'../web/features/chat/activity.js'),'utf8'),ctx);
  vm.runInContext(accessCode+capabilitiesCode+listCode+stateCode+extract('function scheduleCatalog(){','function startCatalog(){'),ctx);
  return {ctx,$,nodes};
}
const cli={source:'cli',runtime:null,readOnly:true,canSend:false,canInterrupt:false,canApprove:false,canReconnect:false};
test('source selector uses full inventory and preserves its open options across unchanged refreshes',()=>{
  const nodes=new Map(),$=id=>{if(!nodes.has(id))nodes.set(id,{hidden:false,value:'',rebuilds:0,replaceChildren(...children){this.children=children;this.rebuilds++;}});return nodes.get(id);};
  const saved=[];let locale='zh-CN';
  const ctx={$ ,clientNavigation:{wslSupported:true},availableSources:['desktop','cli'],listSource:'all',sourceFilterStamp:'',localStorage:{setItem(...args){saved.push(args);}},BridgeI18n:{locale:()=>locale},t:key=>key,el:(tag,cls,label)=>({tag,cls,textContent:label,value:''})};
  vm.createContext(ctx);vm.runInContext(sourceFilterCode,ctx);
  assert.equal(ctx.renderSourceFilter(),true);assert.equal($('source-filter-label').hidden,false);
  assert.deepEqual($('source-filter').children.map(option=>option.value),['all','cli','ide','desktop','wsl']);
  const rebuilds=$('source-filter').rebuilds;ctx.renderSourceFilter();assert.equal($('source-filter').rebuilds,rebuilds);
  locale='en-US';ctx.renderSourceFilter();assert.equal($('source-filter').rebuilds,rebuilds+1);
  ctx.listSource='wsl';ctx.availableSources=['desktop'];assert.equal(ctx.renderSourceFilter(),true);
  assert.equal($('source-filter-label').hidden,false);assert.equal(ctx.listSource,'wsl');assert.equal(saved.length,0);
  ctx.clientNavigation.wslSupported=false;ctx.renderSourceFilter();assert.deepEqual($('source-filter').children.map(option=>option.value),['all','cli','ide','desktop']);assert.equal(ctx.listSource,'all');assert.equal(saved.at(-1)[1],'all');
});
test('every Codex row identifies its source even with a single-source inventory',()=>{
  const {ctx,$}=fixture();$('list-mode').value='recent';
  for(const source of ['desktop','cli','ide','unknown',undefined]){
    ctx.availableSources=source?[source]:[];ctx.listRenderStamp='';
    ctx.listRows=[{id:'a',host:'local',title:'Saved thread',projectName:'Project',projectKey:'p',recency:1,...(source?{access:{source}}:{})}];ctx.renderList();
    const row=$('sessions').children[0];
    assert.equal(row.children[1].cls,'session-source');
    assert.equal(row.children[1].textContent,({desktop:'APP',cli:'CLI',ide:'VS Code',unknown:'Other source'})[source]||'APP');
    assert.equal(row.children[2].tag,'small');assert.equal(row.children[2].children[1].tag,'time');
  }
  assert.equal(ctx.sessionAccess().canSend,true);assert.equal(ctx.sessionAccess(cli).canSend,false);
  assert.equal(ctx.sessionAccess({...cli,readOnly:false,canConfigure:false}).canChangeModel,false);
  assert.equal(ctx.sessionAccess({...cli,readOnly:false,canConfigure:true}).canSelectSkills,true);
});
test('history-only CLI hides join and write controls but keeps history and copy available',()=>{
  const {ctx,$}=fixture();ctx.listRows=[{id:'a',host:'local',access:cli}];ctx.renderState({host:'local',title:'CLI thread',cwd:'/work',access:cli,connected:false,status:'idle',requests:[],submissions:[],historyComplete:true});
  assert.equal($('reconnect').hidden,true);assert.equal($('send').disabled,true);assert.equal($('message').disabled,true);assert.equal($('stop').hidden,true);assert.equal($('permissions-button').disabled,true);assert.equal($('work-mode').disabled,true);assert.equal(ctx.approvals.length,0);
  assert.match($('notice-text').textContent,/CLI/);
});
test('joinable CLI presents an explicit join without enabling unsupported controls',()=>{
  const {ctx,$}=fixture();const joinable={...cli,canReconnect:true};ctx.renderState({host:'local',title:'CLI thread',cwd:'/work',access:joinable,connected:false,status:'idle',requests:[],submissions:[],historyComplete:true});
  assert.equal($('reconnect').hidden,false);assert.equal($('reconnect').textContent,'Continue chat');assert.equal($('send').disabled,true);
  assert.match($('notice-text').textContent,/可先阅读聊天历史/);
});
test('joined CLI enables send, steer and approvals while keeping collaboration modes unavailable',()=>{
  const {ctx,$}=fixture(),access={...cli,readOnly:false,canSend:true,canInterrupt:true,canApprove:true,canReconnect:true};
  ctx.selectedSkills.add('stale-skill');ctx.renderState({host:'local',title:'CLI thread',cwd:'/work',access,connected:true,status:'active',requests:[{id:1}],submissions:[],historyComplete:true});
  assert.equal($('send').disabled,false);assert.equal($('send-mode').disabled,false);assert.equal($('work-mode').disabled,true);
  assert.equal($('stop').hidden,false);assert.equal(ctx.approvals.length,1);assert.equal(ctx.selectedSkills.size,1);
});
test('joined CLI exposes model, Skill, modes and permissions without enabling rename',()=>{
  const {ctx,$}=fixture(),access={...cli,readOnly:false,canSend:true,canReconnect:true,canChangePermissions:true,canChangeModel:true,canSelectSkills:true,canUseModes:true,canUseGoal:true};
  ctx.selectedSkills.add('draft-skill');ctx.renderState({host:'local',title:'CLI thread',cwd:'/work',access,connected:true,status:'idle',requests:[],submissions:[],historyComplete:true,goalRuntimeAvailable:true});
  assert.equal($('model-button').disabled,false);assert.equal($('skills-button').disabled,false);assert.equal($('work-mode').disabled,false);
  assert.equal($('permissions-button').disabled,false);assert.equal(ctx.catalogLoads,1);
  assert.equal(ctx.selectedSkills.size,1);assert.equal(ctx.modesView.goalRuntimeAvailable,true);
  ctx.renderState({host:'local',title:'CLI thread',cwd:'/work',access:{...cli,canReconnect:true},connected:false,status:'idle',requests:[],submissions:[],historyComplete:true});
  assert.equal(ctx.selectedSkills.size,1);assert.equal(ctx.catalogLoads,1);
});
test('permission dialog binds its save to the runtime that opened it',async()=>{
  const {ctx,$}=fixture();let picker,request;
  ctx.state={connected:true,access:{source:'cli',runtime:'cli'}};
  ctx.currentId='a';ctx.currentHost='local';
  Object.assign(ctx.document,{body:{append(){} },querySelectorAll:()=>[picker?.dialog].filter(Boolean)});
  ctx.el=(tag,cls='',value='')=>Object.assign(node(),{tag,cls,textContent:value,open:false,dataset:{},
    showModal(){this.open=true;},close(){this.open=false;},addEventListener(){},setAttribute(){}});
  ctx.permissionPicker=(dialog,options)=>{picker={dialog,options};};
  ctx.sessionUrl=(id,action,host)=>`${host}/${id}/${action}`;
  ctx.api=async(url,body)=>{request={url,body};return {confirmed:true};};
  ctx.toast=()=>{};
  vm.runInContext(permissionCode,ctx);
  $('permissions-button').onclick();
  assert.equal(picker.options.isCurrent(),true);
  await picker.options.save('full-access');
  assert.equal(request.body.expectedRuntime,'cli');
  assert.equal(request.body.confirmed,true);
  ctx.state={connected:true,access:{source:'cli',runtime:'desktop'}};
  assert.equal(picker.options.isCurrent(),false);
  assert.equal(picker.dialog.open,false);
  ctx.state={connected:true,access:{source:'cli',runtime:'cli'}};
  assert.equal(picker.options.isCurrent(),false,'closed stale dialog cannot revive after a reconnect');
  $('permissions-button').onclick();
  ctx.renderState({host:'local',connected:false,access:{source:'cli',runtime:'cli'},requests:[],submissions:[],historyComplete:true});
  assert.equal(picker.dialog.open,false,'disconnect closes the dialog before a same-runtime reconnect');
});
test('a failed CLI join remains visible through later history renders and clears on connection',async()=>{
  const {ctx,$}=fixture(),access={...cli,canReconnect:true},view={host:'local',title:'CLI thread',cwd:'/work',access,connected:false,status:'idle',requests:[],submissions:[],historyComplete:true};
  ctx.renderState(view);ctx.api=async()=>{throw Error('CLI thread is not loaded');};ctx.sessionUrl=()=>'/api/sessions/a/reconnect';ctx.toast=()=>{};
  vm.runInContext(reconnectCode,ctx);await $('reconnect').onclick();
  assert.equal($('notice-text').textContent,'CLI thread is not loaded');
  ctx.renderState({...view,connectionError:'Transport unavailable'});
  assert.equal($('notice-text').textContent,'CLI thread is not loaded');
  ctx.renderState({...view,connected:true,access:{...access,readOnly:false,canSend:true}});
  assert.equal(ctx.reconnectFailure,null);assert.equal($('notice').hidden,true);
});
test('connection diagnostics is secondary to settings and keeps the original export route',()=>{
  assert.doesNotMatch(html,/<a id="session-diagnostics"/);
  assert.match(html,/<button id="open-session-diagnostics"[^>]*data-i18n="SSH 连接诊断"/);
  assert.match(html,/<h2 id="session-diagnostics-heading" data-i18n="SSH 连接诊断"/);
  assert.match(html,/<dialog id="session-diagnostics-dialog"/);
  assert.match(app,/openingAccess\.source==='desktop'&&openingAccess\.canReconnect/);
  assert.match(app,/const diagnosticsPath='\/api\/sessions\/diagnostics'/);
});
test('dismissed SSH faults stay hidden on refresh, then return after recovery or on another gateway',()=>{
  const nodes=new Map(),$=id=>{if(!nodes.has(id))nodes.set(id,{children:[],replaceChildren(){this.children=[];},append(child){this.children.push(child);child.remove=()=>{this.children=this.children.filter(item=>item!==child);};},get childElementCount(){return this.children.length;}});return nodes.get(id);};
  const ctx={$,window:{BridgeRelayBase:'/relay/a'},location:{origin:'https://bridge.example'},dismissedHostFaults:new Map(),t:value=>value,el:(tag,cls,text)=>({tag,cls,textContent:text,children:[],append(child){this.children.push(child);},setAttribute(){}})};
  vm.createContext(ctx);vm.runInContext(extract('function renderHostErrors(hosts){','function authLost()'),ctx);
  const fault=[{host:'remote-1',label:'Remote',error:'SSH unavailable'}];ctx.renderHostErrors(fault);
  assert.equal($('host-errors').childElementCount,1);
  $('host-errors').children[0].children[1].onclick();ctx.renderHostErrors(fault);
  assert.equal($('host-errors').childElementCount,0);
  ctx.window.BridgeRelayBase='/relay/b';ctx.renderHostErrors(fault);
  assert.equal($('host-errors').childElementCount,1);
  ctx.window.BridgeRelayBase='/relay/a';ctx.renderHostErrors([]);ctx.renderHostErrors(fault);
  assert.equal($('host-errors').childElementCount,1);
});
test('diagnostics summary uses only aggregate fields and old native apps cannot export',async()=>{
  const nodes=new Map(),$=id=>{if(!nodes.has(id))nodes.set(id,{hidden:false,disabled:false,textContent:'',close(){},showModal(){},children:[]});return nodes.get(id);};
  const report={hosts:[{host:'local',status:'ok',phase:'discovery',counts:{cli:2,desktop:1,visible:3,archived:1},privatePath:'/secret',title:'secret title'},{host:'remote-1',status:'unavailable',phase:'ssh',reason:'remote_read_failed',alias:'secret alias'}]};
  const ctx={$,window:{},BridgeHost:{isNativeApp:()=>true},t:key=>key,api:async()=>report};vm.createContext(ctx);
  vm.runInContext(extract("let sessionDiagnosticsText='';","$('archived').onchange"),ctx);
  $('open-session-diagnostics').onclick();await new Promise(setImmediate);
  assert.match($('session-diagnostics-summary').textContent,/CLI: 2/);
  assert.match($('session-diagnostics-summary').textContent,/remote-1|远端电脑 1/);
  assert.doesNotMatch($('session-diagnostics-summary').textContent,/secret|\/secret/);
  assert.equal($('session-diagnostics-export').hidden,true);
  assert.equal($('session-diagnostics-export-note').hidden,false);
  let prevented=false;$('session-diagnostics-export').onclick({preventDefault(){prevented=true;},currentTarget:{href:'#'}});
  assert.equal(prevented,true);
  ctx.window.BridgeMobileCapabilities={diagnosticsExport:true};$('open-session-diagnostics').onclick();await new Promise(setImmediate);
  assert.equal($('session-diagnostics-export').hidden,false);
});

test('WSL source badges remain distinct from CLI and APP without changing access source',()=>{
  const {ctx}=fixture();assert.equal(ctx.sourceLabel({source:'cli',runtime:'wsl'}),'WSL');assert.equal(ctx.sourceLabel({source:'cli',runtime:'cli'}),'CLI');assert.equal(ctx.sourceLabel({source:'desktop',runtime:'desktop'}),'APP');ctx.listRows=[{id:'a',host:'wsl:fixture',title:'WSL',projectName:'P',projectKey:'p',recency:1,access:{source:'cli',runtime:'wsl'}}];ctx.renderList();assert.equal(ctx.$('sessions').children[0].children[1].textContent,'WSL');
});
test('switching from a restricted WSL chat restores APP controls and queue availability',()=>{
  const {ctx,$}=fixture(),queue=node(),steer=node();$('send-mode').querySelector=selector=>selector==='[value=queue]'?queue:steer;ctx.workModes.selection='default';
  ctx.renderState({host:'wsl:fixture',endpointId:'wsl:fixture',access:{...cli,runtime:'wsl'},capabilities:{permissions:false,skills:false,notifications:false,queue:false,plan:false},connected:false,status:'idle',requests:[],submissions:[],historyComplete:true});
  assert.equal($('skills-button').hidden,true);assert.equal($('permissions-button').hidden,true);assert.equal($('notify-button').hidden,true);assert.equal($('work-mode').hidden,true);assert.equal(queue.disabled,true);
  ctx.renderState({host:'local',access:{source:'desktop'},connected:true,status:'idle',requests:[],submissions:[],historyComplete:true});
  assert.equal($('skills-button').hidden,false);assert.equal($('permissions-button').hidden,false);assert.equal($('notify-button').hidden,false);assert.equal($('work-mode').hidden,false);assert.equal(queue.disabled,false);
});

test('desktop router failure offers explicit rebuild and keeps failure plus draft visible',async()=>{
  const {ctx,$}=fixture(),view={host:'local',connected:false,status:'idle',requests:[],submissions:[],historyComplete:true,rebuildRecommended:true,access:{source:'desktop',canReconnect:true}};
  $('message').value='unsent draft';ctx.renderState(view);
  assert.equal($('reconnect').textContent,'重建连接');assert.match($('notice-text').textContent,/建议重建连接/);
  const calls=[];ctx.sessionUrl=(id,action,host)=>`${host}/${id}/${action}`;ctx.api=async(url,body)=>{calls.push({url,body});throw Error('桌面执行连接尚未恢复');};
  vm.runInContext(reconnectCode,ctx);await $('reconnect').onclick();ctx.renderState(view);
  assert.equal(JSON.stringify(calls),JSON.stringify([{url:'local/a/reconnect',body:{activate:true,rebuild:true}}]));
  assert.equal($('reconnect').disabled,false);assert.match($('notice-text').textContent,/尚未恢复/);assert.equal($('message').value,'unsent draft');
});

test('a failed rebuild keeps retry and app management independently available',async()=>{
  const {ctx,$}=fixture(),calls=[];
  const view={host:'local',connected:false,status:'idle',requests:[],submissions:[],historyComplete:true,desktopRestartRecommended:true,rebuildRecommended:false,access:{source:'desktop',canReconnect:true}};
  $('message').value='unsent draft';ctx.renderState(view);
  ctx.clientNavigation={openManager(){calls.push('manager');}};
  ctx.sessionUrl=(id,action,host)=>`${host}/${id}/${action}`;
  ctx.api=async(url,body)=>{calls.push({url,body});return {connected:true};};
  vm.runInContext(reconnectCode,ctx);
  assert.equal($('reconnect').textContent,'重建连接');assert.equal($('reconnect-manager').hidden,false);
  await $('reconnect').onclick();
  assert.equal(JSON.stringify(calls),JSON.stringify([{url:'local/a/reconnect',body:{activate:true,rebuild:true}}]));
  $('reconnect-manager').onclick();assert.equal(calls[1],'manager');assert.equal($('message').value,'unsent draft');
  ctx.renderState({...view,connected:true,desktopRestartRecommended:false});
  assert.equal($('notice').hidden,true);assert.equal($('reconnect-manager').hidden,true);
});
