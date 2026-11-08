'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const source=fs.readFileSync(path.join(__dirname,'../web/shell/app.js'),'utf8');
function section(start,end){const at=source.indexOf(start),last=source.indexOf(end,at);assert.ok(at>=0&&last>at,'shell fixture markers must exist');return source.slice(at,last);}
const environmentsCode=section("let codexAccountEntry='desktop'",'clientNavigation.onApplicationChange=');
const creationCode=section('let newChatProjects=[]','const messageActions=');
const accountCode=section("const codexCliRoot=el('div');",'window.BridgeNavigation=');
const ubuntu='wsl:'+'a'.repeat(32),debian='wsl:'+'b'.repeat(32),disabled='wsl:'+'c'.repeat(32);
function fixture({native=true,listSource='all',host='local',wslSupported=true}={}){
 const nodes=new Map(),calls=[],opened=[],store=new Map(),notices=[];
 function node(tag='div'){
  let value='';const n={tagName:tag.toUpperCase(),children:[],dataset:{},textContent:'',hidden:false,disabled:false,checked:false,open:false,
   get value(){return value;},set value(next){value=String(next);},get options(){return this.children;},
   append(...children){for(const child of children){this.children.push(child);child.parentElement=this;if(this.tagName==='SELECT'&&!value)value=child.value;}},
   replaceChildren(...children){this.children=[];if(this.tagName==='SELECT')value='';this.append(...children);},
   remove(){if(this.parentElement)this.parentElement.children=this.parentElement.children.filter(child=>child!==this);},showModal(){this.open=true;},close(){this.open=false;},addEventListener(){},dispatchEvent(){},setAttribute(key,v){this[key]=v;},closest(selector){return selector==='.picker-head'?this:null;},after(){},
   querySelector(){return this.labelNode||(this.labelNode=node('span'));},querySelectorAll(){return [];},classList:{add(){},remove(){},toggle(){},contains(){return false;}}};return n;
 }
 const html=fs.readFileSync(path.join(__dirname,'../web/index.html'),'utf8');
 for(const match of html.matchAll(/<([a-z]+)\b([^>]*\bid="([^"]+)"[^>]*)>/g)){const n=node(match[1]);n.hidden=/\bhidden\b/.test(match[2]);nodes.set(match[3],n);}
 const $=id=>{assert.ok(nodes.has(id),'unknown shell element '+id);return nodes.get(id);};
 const Option=function(text,value){return Object.assign(node('option'),{textContent:text,value});};
 $('new-chat-type').append(new Option('Codex App','desktop'),new Option('Codex CLI','cli'),new Option('WSL','wsl'));
 const entries=[{id:'desktop',kind:'desktop',enabled:native},{id:ubuntu,kind:'wsl-cli',enabled:true,name:'Ubuntu'},{id:debian,kind:'wsl-cli',enabled:true,name:'Debian'},{id:disabled,kind:'wsl-cli',enabled:false,name:'Stopped'}];
 let handler=async(url,body)=>url.startsWith('/api/projects')?{projects:[]}:{id:'created',host:new URL(url,'https://fixture').searchParams.get('entry')||'local'};
 const api=async(url,body)=>{calls.push({url,body:body===undefined?undefined:JSON.parse(JSON.stringify(body))});return handler(url,body);};
 class AccountsPanel{constructor(options){Object.assign(this,options);this.clears=0;this.refreshButton=node('button');}attachApiAccess(panel,details){this.apiPanel=panel;this.apiDetails=details;}clear(){this.clears++;}visible(){return nodes.get('accounts-dialog').open&&!nodes.get('accounts-content').hidden;}refresh(){return this.read();}}
 class CliAccessPanel{constructor(options){Object.assign(this,options);this.clears=0;this.refreshes=0;}clear(){this.clears++;}refresh(){this.refreshes++;return this.read();}}
 class WslAccountsPanel{constructor(options){Object.assign(this,options);}clear(){}select(entry){this.entry=entry;return this.read(entry);}}
 const close=node('button'),context=vm.createContext({$,api,Option,AccountsPanel,CliAccessPanel,WslAccountsPanel,URL,Event:class{constructor(type){this.type=type;}},window:{BridgeFeatures:{load:async name=>{calls.push({feature:name});}}},BridgeI18n:{t:value=>value,locale:()=> 'zh'},document:{getElementById:$,createElement:node,querySelector:()=>close},
  t:value=>value,el:(tag,_class='',text='')=>Object.assign(node(tag),{textContent:text}),
  clientNavigation:{wslSupported,provider:'codex',clients:[{id:'codex',entries}]},currentHost:host,currentId:null,listSource,listRows:[],
  renderSourceFilter(){},uuid:()=> '11111111-1111-4111-8111-111111111111',setLanguageBusy(){},loadList:async()=>{},openChat:async(id,where)=>opened.push({id,host:where}),toast:message=>notices.push(message),isCodexClient:id=>id==='codex',
  sessionStorage:{getItem:key=>store.get(key)||null,setItem:(key,value)=>store.set(key,value),removeItem:key=>store.delete(key)}});
 require('./fixtures/codex-entry-predicates.cjs')(context);
 vm.runInContext(fs.readFileSync(path.join(__dirname,'../web/features/clients/client-lifecycle.js'),'utf8'),context);
 vm.runInContext(fs.readFileSync(path.join(__dirname,'../web/shared/project-picker.js'),'utf8'),context);
 vm.runInContext(environmentsCode+creationCode+accountCode,context);
 return {$,calls,opened,entries,context,run:code=>vm.runInContext(code,context),setHandler:next=>handler=next};
}
const project=(entry,key)=>({key,name:key,cwd:'/home/'+key,host:entry,hostLabel:entry===ubuntu?'Ubuntu':'Debian',type:'cli'});

test('WSL new-chat picker chooses a registered environment and keeps its project and session routes',async()=>{
 const ui=fixture({listSource:'wsl',host:debian});ui.setHandler(async(url,body)=>url.startsWith('/api/projects')?{projects:[project(debian,'debian-project')]}:{id:'created',host:debian});
 await ui.$('new-chat').onclick();assert.equal(ui.$('new-chat-type').value,'wsl');assert.equal(ui.$('new-chat-entry').value,debian);assert.equal(ui.$('new-chat-entry-label').hidden,false);
 assert.deepEqual(ui.$('new-chat-entry').options.map(option=>option.value),[ubuntu,debian]);
 assert.equal(ui.calls[0].url,'/api/projects?entry='+encodeURIComponent(debian));
 assert.deepEqual(ui.$('new-chat-project').options.map(option=>option.value),[debian+'|independent','__directory__','debian-project']);
 assert.deepEqual(ui.$('new-chat-directory-host').options.map(option=>option.value),[debian]);
 ui.$('new-chat-project').value='debian-project';ui.$('new-chat-project').onchange();assert.equal(ui.$('new-chat-path').textContent,'/home/debian-project');
 await ui.$('new-chat-form').onsubmit({preventDefault(){}});const write=ui.calls.find(call=>call.url.startsWith('/api/sessions'));
 assert.equal(write.url,'/api/sessions?entry='+encodeURIComponent(debian));assert.equal(write.body.project,'debian-project');assert.equal(Object.hasOwn(write.body,'kind'),false);assert.deepEqual(ui.opened,[{id:'created',host:debian}]);
});
test('switching WSL environments ignores a late project response from the previous environment',async()=>{
 const ui=fixture({listSource:'wsl'}),pending=new Map();ui.setHandler(url=>new Promise(resolve=>pending.set(new URL(url,'https://fixture').searchParams.get('entry'),resolve)));
 const first=ui.$('new-chat').onclick();assert.equal(ui.$('new-chat-entry').value,ubuntu);
 ui.$('new-chat-entry').value=debian;const second=ui.$('new-chat-entry').onchange();assert.equal(ui.$('new-chat-project').value,debian+'|independent');assert.equal(ui.$('new-chat-submit').disabled,false);
 pending.get(debian)({projects:[project(debian,'new-project')]});await second;
 pending.get(ubuntu)({projects:[project(ubuntu,'stale-project')]});await first;
 assert.equal(ui.$('new-chat-entry').value,debian);assert.equal(ui.$('new-chat-project').options.some(option=>option.value==='new-project'),true);assert.equal(ui.$('new-chat-project').options.some(option=>option.value==='stale-project'),false);
 assert.deepEqual(ui.$('new-chat-directory-host').options.map(option=>option.value),[debian]);assert.equal(ui.$('new-chat-project').value,debian+'|independent');
});
test('WSL-only Codex defaults to WSL even with a saved native CLI or APP filter',async()=>{
 for(const listSource of ['all','cli','desktop','wsl']){
  const ui=fixture({native:false,listSource});await ui.$('new-chat').onclick();assert.equal(ui.$('new-chat-type').value,'wsl',listSource);
  assert.equal(ui.$('new-chat-type').options.find(option=>option.value==='desktop').disabled,true);assert.equal(ui.$('new-chat-type').options.find(option=>option.value==='cli').disabled,true);assert.equal(ui.$('new-chat-type').options.find(option=>option.value==='wsl').disabled,false);
  assert.equal(ui.calls.length,1);assert.equal(ui.calls[0].url,'/api/projects?entry='+encodeURIComponent(ubuntu));
 }
});
test('WSL directory project creation and browsing use only the selected environment',async()=>{
 const ui=fixture({listSource:'wsl',host:ubuntu});ui.setHandler(async(url,body)=>{
  if(url.startsWith('/api/projects/browse'))return {path:'/home/fixture',home:'/home/fixture',parent:'/home',roots:[],directories:[],identity:'folder-id'};
  if(url.startsWith('/api/projects'))return body?{project:{key:'wsl-new-project'}}:{projects:[]};
  return {id:'created',host:ubuntu};
 });
 await ui.$('new-chat').onclick();ui.$('new-chat-project').value='__directory__';ui.$('new-chat-project').onchange();assert.equal(ui.$('new-chat-directory').hidden,false);
 await ui.$('new-chat-directory-browse').onclick();assert.equal(ui.calls.at(-1).url,'/api/projects/browse?entry='+encodeURIComponent(ubuntu));assert.equal(ui.calls.at(-1).body.host,ubuntu);
 ui.$('folder-picker-select').onclick();await ui.$('new-chat-form').onsubmit({preventDefault(){}});
 const creation=ui.calls.find(call=>call.url.startsWith('/api/projects?')&&call.body);assert.equal(creation.url,'/api/projects?entry='+encodeURIComponent(ubuntu));assert.deepEqual(creation.body,{path:'/home/fixture',createDirectory:false,host:ubuntu});
 const session=ui.calls.find(call=>call.url.startsWith('/api/sessions'));assert.equal(session.body.project,'wsl-new-project');assert.equal(session.url,'/api/sessions?entry='+encodeURIComponent(ubuntu));
});
test('switching account environments routes WSL reads and edits without invoking native CLI access',async()=>{
 const ui=fixture();ui.setHandler(async()=>({accounts:[],current:{kind:'external'},management:{external:true}}));
 ui.run("renderAccountEntries();$('accounts-dialog').showModal();accountEntrySelect.value='"+ubuntu+"';accountEntrySelect.onchange();");await new Promise(setImmediate);
 assert.equal(ui.run('codexCliDetails.hidden'),true);assert.ok(ui.run('codexCliAccessPanel.clears')>0);assert.equal(ui.calls.some(call=>call.url==='/api/cli-access'),false);
 await ui.run("codexWslAccounts.request({provider:'codex',entry:codexWslAccounts.entry,action:'switch',id:'saved'})");assert.equal(ui.calls.at(-1).url,'/api/wsl-accounts');assert.equal(ui.calls.at(-1).body.entry,ubuntu);assert.equal(ui.calls.at(-1).body.action,'switch');
 ui.run("accountEntrySelect.value='"+debian+"';accountEntrySelect.onchange();");await new Promise(setImmediate);assert.equal(ui.calls.at(-1).url,'/api/wsl-accounts?provider=codex&entry='+encodeURIComponent(debian));assert.equal(ui.calls.some(call=>call.url==='/api/cli-access'),false);
 ui.run("accountEntrySelect.value='desktop';accountEntrySelect.onchange();");await new Promise(setImmediate);assert.equal(ui.run('codexCliDetails.hidden'),false);assert.equal(ui.calls.at(-1).url,'/api/cli-access');
});
test('opening accounts from an active WSL chat selects its environment without a local request',async()=>{
 const ui=fixture({native:false,host:ubuntu});ui.setHandler(async()=>({accounts:[],current:{kind:'external'},management:{external:true}}));assert.equal(ui.run('openCurrentAccounts()'),true);await new Promise(setImmediate);
 assert.equal(ui.run('codexAccountEntry'),ubuntu);assert.equal(ui.run('codexCliDetails.hidden'),true);assert.equal(ui.calls.filter(call=>call.url).length,1);assert.equal(ui.calls.find(call=>call.url).url,'/api/wsl-accounts?provider=codex&entry='+encodeURIComponent(ubuntu));
});

test('a stopped WSL can create an independent chat when passive project inventory fails',async()=>{
 const ui=fixture({native:false,listSource:'wsl'});ui.setHandler(async(url,body)=>{if(url.startsWith('/api/projects'))throw Error('WSL is stopped');return {id:'created',host:ubuntu};});
 await ui.$('new-chat').onclick();assert.equal(ui.$('new-chat-error').textContent,'WSL is stopped');assert.equal(ui.$('new-chat-project').value,ubuntu+'|independent');assert.equal(ui.$('new-chat-submit').disabled,false);
 await ui.$('new-chat-form').onsubmit({preventDefault(){}});assert.equal(ui.calls.at(-1).url,'/api/sessions?entry='+encodeURIComponent(ubuntu));assert.equal(ui.calls.at(-1).body.project,ubuntu+'|independent');assert.deepEqual(ui.opened,[{id:'created',host:ubuntu}]);
 assert.equal(ui.calls.some(call=>call.url==='/api/codex/entries'),false,'passive inventory does not connect or start WSL');
});
test('submitting an independent chat invalidates an outstanding project read',async()=>{
 const ui=fixture({listSource:'wsl'});let resolveProjects,resolveCreate;ui.setHandler(url=>new Promise(resolve=>url.startsWith('/api/projects')?resolveProjects=resolve:resolveCreate=resolve));
 const opening=ui.$('new-chat').onclick(),creating=ui.$('new-chat-form').onsubmit({preventDefault(){}});
 resolveProjects({projects:[project(ubuntu,'late-project')]});await opening;assert.equal(ui.$('new-chat-project').options.some(option=>option.value==='late-project'),false);assert.equal(ui.run('creatingChat'),true);
 resolveCreate({id:'created',host:ubuntu});await creating;assert.deepEqual(ui.opened,[{id:'created',host:ubuntu}]);
});
test('stopping the current environment returns to the list without affecting another environment',()=>{
 const ui=fixture({host:ubuntu});let left=0;ui.run("currentId='active-thread';");ui.context.clientNavigation.onApplicationChange=()=>left++;
 vm.runInContext(section('clientNavigation.onClientStates=', 'desktopSessions.onListEnter='),ui.context);
 const native=ui.entries.find(row=>row.id==='desktop'),wsl=ui.entries.find(row=>row.id===ubuntu);native.enabled=false;ui.context.clientNavigation.onClientStates(ui.context.clientNavigation.clients);assert.equal(left,0);
 wsl.enabled=false;ui.context.clientNavigation.onClientStates(ui.context.clientNavigation.clients);assert.equal(left,1);
});

test('Codex CLI exposes configured SSH environments before any project exists and registers the selected host and kind',async()=>{
 const ui=fixture({listSource:'cli',host:'build-host'});
 ui.setHandler(async(url,body)=>{
  if(url==='/api/projects/browse')return {path:'/srv/work',home:'/home/fixture',parent:'/srv',roots:[],directories:[],identity:'ssh-directory'};
  if(url==='/api/projects')return body?{project:{key:'cli|build-host|new'}}:{projects:[],environments:[{key:'cli|local',type:'cli',host:'local',name:'此电脑 · Codex CLI'},{key:'cli|build-host',type:'cli',host:'build-host',name:'Build host · Codex CLI'}]};
  return {id:'created',host:'build-host'};
 });
 await ui.$('new-chat').onclick();assert.equal(ui.$('new-chat-entry').value,'cli|build-host');assert.equal(ui.$('new-chat-entry-label').hidden,false);assert.equal(ui.$('new-chat-submit').disabled,true);
 await ui.$('new-chat-directory-browse').onclick();assert.equal(ui.calls.at(-1).body.host,'build-host');assert.equal(ui.calls.at(-1).body.name,null);
 ui.$('folder-picker-select').onclick();assert.equal(ui.$('new-chat-project').value,'__directory__');assert.equal(ui.$('new-chat-submit').disabled,false);
 await ui.$('new-chat-form').onsubmit({preventDefault(){}});
 const register=ui.calls.find(call=>call.url==='/api/projects'&&call.body);assert.deepEqual(register.body,{host:'build-host',path:'/srv/work',createDirectory:false,kind:'cli'});
 const create=ui.calls.find(call=>call.url==='/api/sessions');assert.equal(create.body.kind,'cli');assert.equal(create.body.project,'cli|build-host|new');assert.deepEqual(ui.opened,[{id:'created',host:'build-host'}]);
});

test('changing the selected WSL invalidates an open directory picker before it can create a folder',async()=>{
 const ui=fixture({listSource:'wsl'});ui.setHandler(async url=>url.startsWith('/api/projects/browse')?{path:'/home/fixture',home:'/home/fixture',parent:'/home',roots:[],directories:[],identity:'old-environment'}:{projects:[]});
 await ui.$('new-chat').onclick();await ui.$('new-chat-directory-browse').onclick();
 ui.$('new-chat-entry').value=debian;await ui.$('new-chat-entry').onchange();const count=ui.calls.length;
 ui.$('folder-picker-name').value='must-not-create';await ui.$('folder-picker-create').onclick();
 assert.equal(ui.calls.length,count);assert.equal(ui.$('folder-picker').open,false);assert.equal(ui.$('new-chat-directory-path').value,'');
});

test('non-Windows gateway omits WSL form controls and does not load or query WSL',async()=>{
 const ui=fixture({wslSupported:false,listSource:'wsl'});await ui.$('new-chat').onclick();ui.run('openCurrentAccounts()');await new Promise(setImmediate);
 assert.equal(ui.$('new-chat-type').options.some(option=>option.value==='wsl'),false);assert.equal(ui.run('accountEntryControls'),null);assert.equal(ui.run('codexWslAccounts'),null);
 assert.equal(ui.calls.some(call=>call.feature==='wsl'||call.url?.includes('wsl')||call.url?.includes('entry=')),false);
});

test('selecting an SSH environment reads only its projects and ignores a late response after switching local',async()=>{
 const ui=fixture({listSource:'cli'}),environments=[{key:'cli|local',type:'cli',host:'local',name:'Local'},{key:'cli|ssh',type:'cli',host:'ssh',name:'SSH'}];
 let remote;
 ui.setHandler(url=>url==='/api/projects'?Promise.resolve({projects:[project('local','local-project')],environments}):new Promise(resolve=>{assert.equal(url,'/api/projects?host=ssh');remote=resolve;}));
 await ui.$('new-chat').onclick();assert.equal(ui.calls.length,1);
 ui.$('new-chat-entry').value='cli|ssh';const loading=ui.$('new-chat-entry').onchange();
 assert.equal(ui.$('new-chat-project').disabled,true);
 remote({projects:[project('ssh','discovered-ssh-project')]});await loading;
 assert.equal(ui.$('new-chat-project').value,'discovered-ssh-project');assert.equal(ui.$('new-chat-project').disabled,false);
 const second=ui.$('new-chat-entry').onchange();ui.$('new-chat-entry').value='cli|local';await ui.$('new-chat-entry').onchange();
 remote({projects:[project('ssh','stale-project')]});await second;
 assert.equal(ui.$('new-chat-project').value,'local-project');assert.equal(ui.$('new-chat-submit').disabled,false);
 assert.deepEqual(ui.calls.map(row=>row.url),['/api/projects','/api/projects?host=ssh','/api/projects?host=ssh']);
});
test('opening new CLI chat from an SSH conversation completes its selected project inventory once',async()=>{
 const ui=fixture({listSource:'cli',host:'ssh'});
 ui.setHandler(async url=>url==='/api/projects'?{projects:[],environments:[{key:'cli|ssh',type:'cli',host:'ssh',name:'SSH'}]}:{projects:[project('ssh','first-ssh-project')]});
 await ui.$('new-chat').onclick();assert.equal(ui.$('new-chat-project').value,'first-ssh-project');
 assert.deepEqual(ui.calls.map(row=>row.url),['/api/projects','/api/projects?host=ssh']);
});
