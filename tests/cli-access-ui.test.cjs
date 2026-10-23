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
const newChatTypeCode=extract("$('new-chat-type').onchange=", "$('new-chat-project').onchange=");
test('CLI creation picker enables only a known CLI folder and uses an accurate action',()=>{
  const nodes=new Map(),$=id=>{if(!nodes.has(id))nodes.set(id,{value:'',textContent:'',disabled:false,children:[],replaceChildren(){this.children=[];this.value='';},append(option){this.children.push(option);this.value=option.value;},onchange(){}});return nodes.get(id);};
  const ctx={$,listRows:[],currentId:null,currentHost:'local',newChatProjects:[],Option:class{constructor(text,value){this.text=text;this.value=value;}},t:key=>({'创建聊天':'Create chat','创建并打开':'Create and open'}[key]||key)};
  vm.createContext(ctx);vm.runInContext(newChatTypeCode,ctx);
  $('new-chat-type').value='cli';$('new-chat-type').onchange();
  assert.equal($('new-chat-submit').disabled,true);
  assert.equal($('new-chat-submit').textContent,'Create chat');
  assert.equal($('new-chat-project').children.some(option=>option.value==='__directory__'),false);
  ctx.newChatProjects=[{type:'cli',host:'local',key:'cli|local|folder',name:'agent_chat',cwd:'/fixture/agent_chat'}];
  $('new-chat-type').onchange();
  assert.equal($('new-chat-project').value,'cli|local|folder');
  assert.equal($('new-chat-submit').disabled,false);
  $('new-chat-type').value='desktop';$('new-chat-type').onchange();
  assert.equal($('new-chat-submit').textContent,'Create and open');
  assert.equal($('new-chat-project').children.some(option=>option.value==='__directory__'),true);
});
function node(){return {hidden:false,disabled:false,value:'',textContent:'',title:'',children:[],scrollTop:0,classList:{toggle(){}},append(...parts){this.children.push(...parts);},replaceChildren(...parts){this.children=parts;},querySelector(){return node();}};}
function fixture(){
  const nodes=new Map();const $=id=>{if(!nodes.has(id))nodes.set(id,node());return nodes.get(id);};
  const ctx={$ ,state:null,reconnectFailure:null,listRows:[],availableSources:[],listRenderStamp:'',listComplete:false,listReadFinished:false,listHasMore:false,currentId:'a',currentHost:'local',sending:false,catalogStarted:false,catalogLoads:0,startCatalog(){ctx.catalogLoads++;},selectedSkills:new Set(),invalidSkills:new Set(),renderSkillPills(){},BridgeI18n:{locale:()=> 'en-US'},t:key=>({'此电脑':'This computer','其他来源':'Other source','历史记录':'History','重新连接':'Reconnect','继续会话':'Continue chat'}[key]||key),localStorage:{getItem:()=>null,setItem(){}},dateText:()=> 'today',el:(tag,cls='',value='')=>Object.assign(node(),{tag,cls,textContent:value,dataset:{}}),renderActivity(){},contextUsageControl:{update(){}},fastModeControl:{sync(){}},visibleChat:()=>false,sessionActivity:{read(){}},attachments:{ready:()=>true},workModes:{render(view){ctx.modesView=view;}},workbench:{current:{}},chatTimeline:{refreshActions(){}},renderApprovals(rows){ctx.approvals=rows;},renderQueue(){},approvalStamp:'',chatKey:()=> 'local|a'};
  vm.createContext(ctx);vm.runInContext(accessCode+capabilitiesCode+listCode+stateCode,ctx);
  return {ctx,$,nodes};
}
const cli={source:'cli',runtime:null,readOnly:true,canSend:false,canInterrupt:false,canApprove:false,canReconnect:false};
test('source selector uses full inventory and preserves its open options across unchanged refreshes',()=>{
  const nodes=new Map(),$=id=>{if(!nodes.has(id))nodes.set(id,{hidden:false,value:'',rebuilds:0,replaceChildren(...children){this.children=children;this.rebuilds++;}});return nodes.get(id);};
  const saved=[];let locale='zh-CN';
  const ctx={$ ,availableSources:['desktop','cli'],listSource:'all',sourceFilterStamp:'',localStorage:{setItem(...args){saved.push(args);}},BridgeI18n:{locale:()=>locale},t:key=>key,el:(tag,cls,label)=>({tag,cls,textContent:label,value:''})};
  vm.createContext(ctx);vm.runInContext(sourceFilterCode,ctx);
  assert.equal(ctx.renderSourceFilter(),true);assert.equal($('source-filter-label').hidden,false);
  assert.deepEqual($('source-filter').children.map(option=>option.value),['all','desktop','cli']);
  const rebuilds=$('source-filter').rebuilds;ctx.renderSourceFilter();assert.equal($('source-filter').rebuilds,rebuilds);
  locale='en-US';ctx.renderSourceFilter();assert.equal($('source-filter').rebuilds,rebuilds+1);
  ctx.listSource='cli';ctx.availableSources=['desktop'];assert.equal(ctx.renderSourceFilter(),false);
  assert.equal($('source-filter-label').hidden,true);assert.equal(ctx.listSource,'all');assert.deepEqual(saved.at(-1),['codex-source-filter','all']);
});
test('every Codex row identifies its source even with a single-source inventory',()=>{
  const {ctx,$}=fixture();$('list-mode').value='recent';
  for(const source of ['desktop','cli','ide','unknown',undefined]){
    ctx.availableSources=source?[source]:[];ctx.listRenderStamp='';
    ctx.listRows=[{id:'a',host:'local',title:'Saved thread',projectName:'Project',projectKey:'p',recency:1,...(source?{access:{source}}:{})}];ctx.renderList();
    const row=$('sessions').children[0];
    assert.equal(row.children[1].cls,'session-source');
    assert.equal(row.children[1].textContent,({desktop:'APP',cli:'CLI',ide:'IDE',unknown:'Other source'})[source]||'APP');
    assert.equal(row.children[2].tag,'small');assert.equal(row.children[2].children[1].tag,'time');
  }
  assert.equal(ctx.sessionAccess().canSend,true);assert.equal(ctx.sessionAccess(cli).canSend,false);
  assert.equal(ctx.sessionAccess({...cli,readOnly:false,canConfigure:false}).canChangeModel,false);
  assert.equal(ctx.sessionAccess({...cli,readOnly:false,canConfigure:true}).canSelectSkills,true);
});
test('history-only CLI hides join and write controls but keeps history and copy available',()=>{
  const {ctx,$}=fixture();ctx.listRows=[{id:'a',host:'local',access:cli}];ctx.renderState({host:'local',title:'CLI thread',cwd:'/work',access:cli,connected:false,status:'idle',requests:[],submissions:[],historyComplete:true});
  assert.equal($('reconnect').hidden,true);assert.equal($('send').disabled,true);assert.equal($('message').disabled,true);assert.equal($('stop').hidden,true);assert.equal($('permissions-button').disabled,true);assert.equal($('rename-chat').disabled,true);assert.equal($('work-mode').disabled,true);assert.equal(ctx.approvals.length,0);
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
  assert.equal($('send').disabled,false);assert.equal($('send-mode').disabled,false);assert.equal($('work-mode').disabled,true);assert.equal($('rename-chat').disabled,true);
  assert.equal($('stop').hidden,false);assert.equal(ctx.approvals.length,1);assert.equal(ctx.selectedSkills.size,1);
});
test('joined CLI exposes model, Skill, modes and permissions without enabling rename',()=>{
  const {ctx,$}=fixture(),access={...cli,readOnly:false,canSend:true,canReconnect:true,canChangePermissions:true,canChangeModel:true,canSelectSkills:true,canUseModes:true,canUseGoal:true};
  ctx.selectedSkills.add('draft-skill');ctx.renderState({host:'local',title:'CLI thread',cwd:'/work',access,connected:true,status:'idle',requests:[],submissions:[],historyComplete:true,goalRuntimeAvailable:true});
  assert.equal($('model-button').disabled,false);assert.equal($('skills-button').disabled,false);assert.equal($('work-mode').disabled,false);
  assert.equal($('permissions-button').disabled,false);assert.equal($('rename-chat').disabled,true);assert.equal(ctx.catalogLoads,1);
  assert.equal(ctx.selectedSkills.size,1);assert.equal(ctx.modesView.goalRuntimeAvailable,true);
  ctx.renderState({host:'local',title:'CLI thread',cwd:'/work',access:{...cli,canReconnect:true},connected:false,status:'idle',requests:[],submissions:[],historyComplete:true});
  assert.equal(ctx.selectedSkills.size,1);assert.equal(ctx.catalogLoads,1);
});
test('permission dialog binds its save to the runtime that opened it',async()=>{
  const {ctx,$}=fixture();let picker,request;
  ctx.state={connected:true,access:{source:'cli',runtime:'cli'}};
  ctx.currentId='a';ctx.currentHost='local';
  ctx.document={body:{append(){} },querySelectorAll:()=>[picker?.dialog].filter(Boolean)};
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
