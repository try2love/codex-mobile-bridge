'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path'),{webcrypto}=require('node:crypto');
test('loaded desktop chat content reports readiness after rendering, including saved history',async()=>{
 const ui=await opened();let ready=0;
 ui.view.onDetailReady=()=>{assert.match(ui.view.messages.textContent,/Latest reply/);ready++;};
 const detail={session:{id:'a',title:'A'},messages:[{id:'reply',role:'assistant',text:'Latest reply'}]};
 ui.view.applyDetail(detail,true);assert.equal(ready,0);
 ui.view.applyDetail(detail);assert.equal(ready,1);
 ui.view.applyDetail({...detail,connected:false});assert.equal(ready,2);
});

test('opening the current desktop chat keeps its draft, message nodes and workbench',async()=>{
 const ui=await opened(),view=ui.view,session=view.workbench.current;
 ui.setHandler(async()=>({session:{id:'a',title:'Chat',status:'idle'},messages:[{id:'reply',role:'assistant',text:'Current reply'}]}));await view.open('a');
 view.input.value='unsent draft';Object.defineProperty(view.messages,'scrollHeight',{value:1000,configurable:true});view.messages.scrollTop=42;const nodes=[...view.messageList.children];
 const prepare=view.prepareChat;view.prepareChat=()=>{throw Error('chat rebuilt');};
 await view.open(view.sid);
 assert.equal(view.workbench.current,session);assert.equal(view.input.value,'unsent draft');
 assert.equal(view.messages.scrollTop,42);assert.equal(view.messageList.children.length,nodes.length);
 assert.ok(nodes.every((node,index)=>node===view.messageList.children[index]));
 view.prepareChat=prepare;
});
function fixture({storage=new Map(),sessionStore=new Map()}={}){
 const listeners={};let document;
 class Node {
  constructor(tag='div'){this.nodeType=tag==='#text'?3:1;this.tagName=tag.toUpperCase();this.children=[];this.parentElement=null;this.dataset={};this.attributes={};this.hidden=false;this.disabled=false;this.value='';this.className='';this._text='';this.style={setProperty(){}};this.scrollHeight=100;this.scrollTop=0;this.clientHeight=100;this.clientWidth=1000;this.open=false;this.options=this.children;this.classList={contains:n=>this.className.split(' ').includes(n),add:(...ns)=>this.className=[...new Set([...this.className.split(' ').filter(Boolean),...ns])].join(' '),remove:(...ns)=>this.className=this.className.split(' ').filter(n=>!ns.includes(n)).join(' '),toggle:(n,v)=>{v=v??!this.classList.contains(n);this.classList[v?'add':'remove'](n);return v;}};}
  get textContent(){return this._text+this.children.map(n=>n.textContent).join('');}set textContent(v){this._text=String(v??'');this.children=[];this.options=this.children;}
  getBoundingClientRect(){return {width:this.clientWidth,top:0,bottom:this.clientHeight};}
  get ownerDocument(){return document;}
  get firstElementChild(){return this.children[0];}get firstChild(){return this.children[0]||null;}get nextSibling(){return this.parentElement?.children[this.parentElement.children.indexOf(this)+1]||null;}get lastChild(){return this.children.at(-1);}get childNodes(){return this.children;}get isConnected(){return !!this.parentElement;}
  append(...nodes){for(let n of nodes){if(typeof n==='string')n=document.createTextNode(n);n.remove();n.parentElement=this;this.children.push(n);}}
  prepend(...nodes){for(const n of [...nodes].reverse()){n.remove();n.parentElement=this;this.children.unshift(n);}}
  replaceChildren(...nodes){for(const n of this.children)n.parentElement=null;this.children=[];this.options=this.children;this._text='';this.append(...nodes);}
  remove(){if(this.parentElement){const p=this.parentElement;p.children.splice(p.children.indexOf(this),1);this.parentElement=null;}}
  after(...nodes){const p=this.parentElement;if(!p)return;let i=p.children.indexOf(this)+1;for(const n of nodes){n.remove();n.parentElement=p;p.children.splice(i++,0,n);}}
  insertBefore(n,next){n.remove();n.parentElement=this;const i=this.children.indexOf(next);this.children.splice(i<0?this.children.length:i,0,n);}
  add(option){this.append(option);if(this.children.length===1)this.value=option.value;}
  setAttribute(k,v){this.attributes[k]=String(v);if(k==='class')this.className=v;if(k.startsWith('data-'))this.dataset[k.slice(5).replace(/-([a-z])/g,(_,c)=>c.toUpperCase())]=v;}
  getAttribute(k){return this.attributes[k];}removeAttribute(k){delete this.attributes[k];}addEventListener(type,fn){this['on'+type]=fn;}removeEventListener(type,fn){if(this['on'+type]===fn)this['on'+type]=null;}
  matches(selector){return selector.split(',').some(s=>{s=s.trim();if(s==='input:checked')return this.tagName==='INPUT'&&this.checked;if(s.startsWith('.'))return s.slice(1).split('.').every(name=>this.classList.contains(name));if(s.startsWith('#'))return this.id===s.slice(1);const attr=s.match(/^\[([^=\]]+)(?:="?([^"\]]+)"?)?\]$/);if(attr)return attr[2]===undefined?this.attributes[attr[1]]!==undefined:this.attributes[attr[1]]===attr[2];return this.tagName===s.toUpperCase();});}
  closest(selector){return this.matches(selector)?this:this.parentElement?.closest(selector)||null;}
  querySelectorAll(selector){return this.children.flatMap(n=>[...(n.matches(selector)?[n]:[]),...n.querySelectorAll(selector)]);}querySelector(selector){return this.querySelectorAll(selector)[0]||null;}
  showModal(){this.open=true;}close(){this.open=false;this.onclose?.();}focus(){document.activeElement=this;}select(){}setSelectionRange(start,end){this.selectionStart=start;this.selectionEnd=end;}click(){return this.onclick?.({preventDefault(){}});}contains(n){return n===this||this.children.some(c=>c.contains(n));}
 }
 document={body:new Node('body'),documentElement:new Node('html'),hidden:false,activeElement:null,getSelection:()=>null,createElement:tag=>new Node(tag),createElementNS:(_,tag)=>new Node(tag),createTextNode:text=>{const n=new Node('#text');n.textContent=text;return n;},querySelectorAll:s=>document.body.querySelectorAll(s),querySelector:s=>document.body.querySelector(s),getElementById:id=>document.body.querySelector('#'+id),addEventListener:(type,fn)=>listeners[type]=fn,dispatchEvent:event=>listeners[event.type]?.(event)};
 const root=new Node('div');root.className='app';document.body.append(root);const select=new Node('select'),calls=[],notices=[];let uploadHandler=async()=>({ok:true,status:200,json:async()=>({image:true,size:16,name:'image.png'})}),handler=async(url,body)=>body?{status:'accepted'}:url.includes('/detail')?{session:{id:'a',title:'Chat',model:'model-a',effort:'high',status:'idle',capabilities:{}},messages:[]}:url.includes('/workspace')?{entries:[],total:0,project:'/project'}:{sessions:[]};
 const context=vm.createContext({CompactComposer:class{render(){}},document,window:{addEventListener:(type,fn)=>listeners['window:'+type]=fn,dispatchEvent:event=>listeners['window:'+event.type]?.(event)},navigator:{},location:{pathname:'/'},history:{replaceState(){}},localStorage:{get length(){return storage.size;},key:i=>[...storage.keys()][i],getItem:k=>storage.get(k),setItem:(k,v)=>storage.set(k,v),removeItem:k=>storage.delete(k)},sessionStorage:{get length(){return sessionStore.size;},key:i=>[...sessionStore.keys()][i],getItem:k=>sessionStore.get(k),setItem:(k,v)=>sessionStore.set(k,v),removeItem:k=>sessionStore.delete(k)},crypto:{getRandomValues:array=>webcrypto.getRandomValues(array)},Uint8Array,TextEncoder,Option:class extends Node{constructor(text,value){super('option');this.textContent=text;this.value=value;this.setAttribute('value',value);}},ResizeObserver:class{observe(){}},MutationObserver:class{observe(){}},AbortController,URLSearchParams,requestAnimationFrame:fn=>fn(),cancelAnimationFrame(){},getComputedStyle:()=>({getPropertyValue:()=>8}),setTimeout(){return 1;},clearTimeout(){},fetch:async(url,options)=>{calls.push({url,upload:options});return uploadHandler(url,options);},renderMarkdown:(node,text)=>node.textContent=text,CustomEvent:class{constructor(type,options){this.type=type;Object.assign(this,options);}}});
 vm.runInContext(fs.readFileSync(path.join(__dirname,'../web/shell/app.js'),'utf8').match(/^function uuid\(\).*$/m)[0],context);
 for(const file of ['web/layouts/viewport.js','web/features/chat/activity.js','web/features/chat/composer-drafts.js','web/features/chat/composer-quotes.js','web/features/chat/attachments.js','web/shared/i18n.js','web/features/clients/client-lifecycle.js','web/features/chat/message-actions.js','web/features/chat/permissions.js','web/features/chat/modes.js','web/features/workspace/workbench.js','web/features/chat/list-sync.js','web/shared/creation-model-picker.js','web/features/clients/desktop-sessions.js'])vm.runInContext(fs.readFileSync(path.join(__dirname,'..',file),'utf8'),context);
 const View=vm.runInContext('DesktopSessionsView',context),view=new View({root,select,request:(url,body,signal)=>{calls.push({url,body});return handler(url,body,signal);},notify:text=>notices.push(text),onSelect(){},csrf:()=>'csrf-test',onUnauthorized(){}});
 return {view,root,document,calls,notices,storage,sessionStore,setHandler:fn=>handler=fn,setUploadHandler:fn=>uploadHandler=fn,run:s=>vm.runInContext(s,context),text:n=>n.textContent};
}
async function opened(){const ui=fixture();await ui.view.choose('claude');await ui.view.open('a');return ui;}
test('independent desktops keep the same native session in separate drafts attachments and workspace routes',async()=>{
 const ui=fixture(),view=ui.view,a='claude:'+ 'a'.repeat(32),b='claude:'+ 'b'.repeat(32);
 view.clientStates=[a,b].map(id=>({id,family:'claude',enabled:true,connected:true}));
 await view.choose(a);await view.open('a');view.input.value='draft A';view.saveDraft();
 const first=view.workbench.current,keyA=view.attachments.key;
 await view.choose(b);await view.open('a');assert.equal(view.input.value,'');assert.notEqual(view.attachments.key,keyA);
 view.input.value='draft B';view.saveDraft();
 assert.notEqual(first,view.workbench.current);
 assert.equal(new URL(view.workbench.endpoint(first,'workspace'),'http://test').searchParams.get('entry'),a);
 assert.equal(new URL(view.attachmentUrl(keyA,'upload','preview'),'http://test').searchParams.get('entry'),a);
 await view.choose(a);await view.open('a');assert.equal(view.input.value,'draft A');
 const catalog=new URL(view.path('create-models',null,b,{environment:'code',refresh:'true'}),'http://test');
 assert.equal(catalog.pathname,'/api/desktop-sessions/claude/create-models');assert.equal(catalog.searchParams.get('entry'),b);assert.equal(catalog.searchParams.get('refresh'),'true');
 assert.ok(ui.calls.every(call=>!call.url?.includes('/claude:')));
});
test('late detail from another instance cannot replace the current same-id conversation',async()=>{
 const ui=fixture(),view=ui.view,a='deepseek:'+ 'a'.repeat(32),b='deepseek:'+ 'b'.repeat(32);let complete;
 view.clientStates=[a,b].map(id=>({id,family:'deepseek',enabled:true,connected:true}));
 ui.setHandler(async url=>{
  const parsed=new URL(url,'http://test');if(!url.includes('/detail'))return {sessions:[]};
  if(parsed.searchParams.get('entry')===a)return new Promise(resolve=>complete=resolve);
  return {session:{id:'same',title:'B',status:'idle'},messages:[{id:'b',role:'assistant',text:'B only'}]};
 });
 await view.choose(a);const old=view.open('same');await Promise.resolve();await view.choose(b);await view.open('same');
 complete({session:{id:'same',title:'A',status:'idle'},messages:[{id:'a',role:'assistant',text:'A private'}]});await old;
 assert.equal(view.title.textContent,'B');assert.match(view.messageList.textContent,/B only/);assert.doesNotMatch(view.messageList.textContent,/A private/);
});
test('native context reuses activity folding and retains explicit expansion after refresh',async()=>{
 const ui=await opened(),view=ui.view;view.provider='deepseek';
 const message={id:'context',role:'activity',kind:'context',title:'上下文',text:'Exact native context'};
 const detail={connected:true,session:{id:'a',title:'Chat',status:'idle'},messages:[message]};
 view.applyDetail(detail);const node=view.messageNodes.get('context').node;
 assert.equal(node.tagName,'DETAILS');assert.equal(!!node.open,false);assert.match(node.textContent,/Exact native context/);
 node.open=true;view.applyDetail(detail);assert.equal(view.messageNodes.get('context').node,node);assert.equal(node.open,true);
});
test('native history images use the shared preview markup and survive unchanged refreshes',async()=>{
 const ui=await opened(),view=ui.view,id='a'.repeat(64),message={id:'image',role:'user',text:'',attachments:[{type:'image',name:'photo.png',desktopId:id}]};
 const detail={connected:true,session:{id:'a',title:'Chat',status:'idle'},messages:[message]};
 view.applyDetail(detail);const node=view.messageNodes.get('image').node;
 assert.equal(node.querySelector('.attachment-image').src,view.path('images/'+id,'a','claude'));
 assert.equal(node.querySelector('.timeline-attachments').children.length,2);
 view.applyDetail(detail);assert.equal(view.messageNodes.get('image').node,node);
 view.provider='deepseek';view.messageNodes.clear();view.applyDetail(detail);
 assert.match(view.messageNodes.get('image').node.querySelector('.attachment-image').src,/deepseek/);
});
test('ZCode project image exposes both the shared image viewer and the session file page',async()=>{
 const ui=fixture(),view=ui.view,id='f'.repeat(64),path='/project/assets/photo.png';
 await view.choose('zcode');await view.open('a');
 const message={id:'photo',role:'user',text:'',attachments:[{type:'file',path,name:'photo.png',desktopId:id}]};
 view.applyDetail({connected:true,session:{id:'a',title:'Chat',status:'idle',cwd:'/project'},messages:[message],
   files:[{id:'f',name:'photo.png',path:'assets/photo.png',reference:path}]});
 const node=view.messageNodes.get('photo').node;
 assert.equal(node.querySelector('.attachment-image').src,view.path('images/'+id,'a','zcode'));
 const file=node.querySelectorAll('a').find(a=>a.textContent==='photo.png');
 assert.equal(file.href,view.workbench.url(view.workbench.current,'download','assets/photo.png'));
 assert.match(file.href,/desktop-sessions\/zcode\/workspace\/download/);
});
test('shared task indicator separates native work, approvals, stale reads and completion',async()=>{
 const ui=await opened(),view=ui.view,apply=(status,requests=[])=>view.applyDetail({connected:true,session:{id:'a',title:'Chat',status,requests},messages:[]});
 apply('active');assert.equal(view.progress.dataset.state,'working');assert.equal(view.progress.hidden,false);assert.ok(view.messageList.children.includes(view.progress));
 apply('active',[{id:'approval',tool:'Confirm',input:{}}]);assert.equal(view.progress.dataset.state,'waiting');
 view.staleDetail();assert.equal(view.progress.dataset.state,'unknown');assert.doesNotMatch(view.progress.textContent,/正在工作/);
 apply('blocked');assert.equal(view.progress.dataset.state,'blocked');apply('idle');assert.equal(view.progress.hidden,true);
 view.provider='future-agent';apply('active');assert.equal(view.progress.dataset.state,'working');assert.match(view.progress.textContent,/future-agent/);
 view.prepareChat('b');assert.equal(view.progress.hidden,true);
});
test('notification provider selection skips list loading and the previously open chat',async()=>{
 const ui=fixture(),prepared=[];ui.view.providerViews.set('claude',{rows:[],sid:'previous'});
 ui.view.prepareChat=id=>prepared.push(id);
 await ui.view.choose('claude',{loadList:false});
 assert.equal(ui.view.provider,'claude');assert.deepEqual(prepared,[]);assert.equal(ui.calls.length,0);
});

test('older history prepends messages without adopting old approvals or access',async()=>{
 const ui=await opened(),message=i=>({id:String(i),role:'assistant',text:'Row '+i});
 const current={session:{id:'a',title:'Current',status:'active',requests:[]},capabilities:{send:true},files:[{id:'recent',path:'recent.md',reference:'recent.md'}],messages:[message(3),message(4)],history:{before:'page-a',hasMore:true}};
 ui.view.applyDetail(current);ui.setHandler(async()=>({session:{id:'a',title:'Old',status:'idle',requests:[{id:'expired'}]},capabilities:{send:false},files:[{id:'older',path:'older.md',reference:'older.md'}],messages:[message(1),message(2),message(3)],history:{before:null,hasMore:false}}));
 await ui.view.loadOlderHistory();
 assert.deepEqual(Array.from(ui.view.lastMessages,m=>m.id),['1','2','3','4']);
 assert.equal(ui.view.state.title,'Current');assert.equal(ui.view.state.status,'active');assert.equal(ui.view.state.requests.length,0);assert.equal(ui.view.state.capabilities.send,true);
 ui.view.applyDetail({...current,messages:[message(3),message(4),message(5)]});
 assert.deepEqual(Array.from(ui.view.lastMessages,m=>m.id),['1','2','3','4','5']);
 assert.equal(ui.view.historyPage.hasMore,false);
 assert.deepEqual(Array.from(ui.view.sessionFiles,f=>f.id).sort(),['older','recent']);
});

test('late older history is discarded after switching sessions',async()=>{
 const ui=await opened();ui.view.applyDetail({session:{id:'a',title:'A'},messages:[{id:'new',text:'New'}],history:{before:'a',hasMore:true}});
 let finish;ui.setHandler(()=>new Promise(resolve=>finish=resolve));const older=ui.view.loadOlderHistory();
 ui.view.sid='b';ui.view.detailRevision++;ui.view.historyMessages=[{id:'b',text:'B'}];
 finish({session:{id:'a'},messages:[{id:'old',text:'Private A'}],history:{hasMore:false}});await older;
 assert.deepEqual(Array.from(ui.view.historyMessages,m=>m.id),['b']);
});

test('Claude list reentry paints a bounded identity-scoped snapshot before the live read',async()=>{
 const storage=new Map(),identity='a'.repeat(64),first=fixture({storage});first.view.bindListCache(identity,'gateway.test');
 first.setHandler(async()=>({sessions:[{id:'saved',title:'Saved chat',backend:'code',cwd:'/work',updatedAt:9,capabilities:{send:true}}]}));
 await first.view.choose('claude');first.view.search.value='Saved';first.view.archived.checked=true;first.view.listMode.value='project';first.view.renderList();first.view.rowsRoot.scrollTop=40;first.view.saveListSnapshot('claude');
 const key=first.view.listSnapshotKey('claude'),saved=JSON.parse(storage.get(key));assert.equal(saved.rows.length,1);assert.equal(saved.rows[0].capabilities,undefined);
 const second=fixture({storage});assert.equal(second.sessionStore.size,0);second.view.bindListCache(identity,'gateway.test');let finish;second.setHandler(()=>new Promise(resolve=>finish=resolve));
 const entering=second.view.choose('claude');assert.equal(second.view.rowsRoot.querySelector('.session').dataset.id,'saved');assert.equal(second.view.search.value,'Saved');assert.equal(second.view.archived.checked,true);assert.equal(second.view.listMode.value,'project');assert.equal(second.view.rowsRoot.scrollTop,40);assert.equal(second.view.canMutate(),false);
 finish({sessions:[{id:'fresh',title:'Fresh chat',backend:'code',cwd:'/work',updatedAt:10}]});await entering;assert.equal(second.view.rows[0].id,'fresh');
 const wrongHost=fixture({storage});wrongHost.view.bindListCache(identity,'other.test');assert.equal(wrongHost.view.providerViews.size,0);
 let finishAccountRead;first.setHandler(()=>new Promise(resolve=>finishAccountRead=resolve));const switching=first.view.accountChanged('claude',{activeId:'old'},{activeId:'new'});assert.equal(storage.has(key),false);
 finishAccountRead({sessions:[]});await switching;assert.deepEqual(JSON.parse(storage.get(key)).rows,[]);
 const wrongLogin=fixture({storage});wrongLogin.view.bindListCache('b'.repeat(64),'gateway.test');assert.equal(wrongLogin.view.providerViews.size,0);assert.equal(storage.has(key),false);
});
test('legacy session snapshots remain readable and migrate to durable storage on reentry',async()=>{
 const identity='a'.repeat(64),key=`desktop-list-v1:${identity}:gateway.test:claude`,sessionStore=new Map([[key,JSON.stringify({rows:[{id:'legacy',title:'Legacy chat'}]})]]),ui=fixture({sessionStore});
 ui.view.bindListCache(identity,'gateway.test');assert.equal(ui.view.providerViews.get('claude').rows[0].id,'legacy');
 ui.view.previewCachedList('claude');assert.equal(JSON.parse(ui.storage.get(key)).rows[0].id,'legacy');
});

test('switching computers preserves each authorized provider snapshot while a same-computer account change clears the old one',()=>{
 const storage=new Map(),a='a'.repeat(64),b='b'.repeat(64),c='c'.repeat(64);
 const keyA=`desktop-list-v1:${a}:computer-a.test:claude`,keyB=`desktop-list-v1:${b}:computer-b.test:claude`;
 storage.set(keyA,JSON.stringify({rows:[{id:'chat-a',title:'Computer A'}]}));
 storage.set(keyB,JSON.stringify({rows:[{id:'chat-b',title:'Computer B'}]}));
 const ui=fixture({storage});ui.view.bindListCache(a,'computer-a.test');
 assert.equal(ui.view.providerViews.get('claude').rows[0].id,'chat-a');
 ui.view.bindListCache(b,'computer-b.test');
 assert.equal(storage.has(keyA),true,'switching to B retains A for a later authorized return');
 assert.equal(ui.view.providerViews.get('claude').rows[0].id,'chat-b');
 ui.view.bindListCache(a,'computer-a.test');
 assert.equal(ui.view.providerViews.get('claude').rows[0].id,'chat-a');
 ui.view.bindListCache(c,'computer-a.test');
 assert.equal(storage.has(keyA),false,'a new account on the same computer clears its old snapshot');
});
test('DeepSeek stores only complete list reads and restores its own provider rows',async()=>{
 const storage=new Map(),view=fixture({storage});view.view.bindListCache('a'.repeat(64),'gateway.test');
 view.setHandler(async()=>({sessions:[{id:'partial',title:'Partial'}],complete:false}));await view.view.choose('deepseek');
 const key=view.view.listSnapshotKey('deepseek');assert.equal(storage.has(key),false);
 view.setHandler(async()=>({sessions:[{id:'dsh',title:'Harness chat',backend:'chat'}],complete:true}));await view.view.refreshList();
 assert.equal(JSON.parse(storage.get(key)).rows[0].id,'dsh');assert.equal(storage.has(view.view.listSnapshotKey('claude')),false);
 const restored=fixture({storage});restored.view.bindListCache('a'.repeat(64),'gateway.test');assert.equal(restored.view.providerViews.get('deepseek').rows[0].id,'dsh');assert.equal(restored.view.providerViews.has('claude'),false);
});
test('durable provider snapshots retain at most 200 rows and 512 KiB',async()=>{
 const ui=fixture();ui.view.bindListCache('a'.repeat(64),'gateway.test');
 ui.setHandler(async()=>({sessions:Array.from({length:201},(_,i)=>({id:String(i),title:'Chat '+i}))}));await ui.view.choose('claude');
 const key=ui.view.listSnapshotKey('claude');assert.equal(JSON.parse(ui.storage.get(key)).rows.length,200);
 ui.setHandler(async()=>({sessions:[{id:'large',title:'x'.repeat(512*1024)}]}));await ui.view.refreshList();
 assert.equal(JSON.parse(ui.storage.get(key)).rows.length,200);
});

test('cached provider previews stay inert until client discovery confirms selection',async()=>{
 for(const provider of ['claude','deepseek']){
  const identity='a'.repeat(64),storage=new Map([[`desktop-list-v1:${identity}:gateway.test:${provider}`,JSON.stringify({rows:[{id:'cached',title:'Cached chat',backend:'code',cwd:'/work',updatedAt:9}]})]]),ui=fixture({storage});
  let selected=0,finish;ui.view.onSelect=()=>selected++;ui.view.bindListCache(identity,'gateway.test');ui.setHandler(()=>new Promise(resolve=>finish=resolve));
  assert.equal(ui.view.previewCachedList(provider),true);
  const row=ui.view.rowsRoot.querySelector('.session');assert.equal(row?.textContent.includes('Cached chat'),true);assert.equal(row.disabled,true);assert.equal(ui.view.container.hidden,false);assert.equal(ui.view.sid,null);assert.equal(selected,0);assert.equal(ui.calls.length,0);
  await ui.view.open('cached');await ui.view.create();await ui.view.refresh();await ui.view.refreshList();assert.equal(ui.calls.length,0);
  ui.view.confirmCachedList();assert.equal(ui.view.rowsRoot.querySelector('.session').disabled,false);assert.equal(ui.calls.length,1);
  finish({sessions:[{id:'fresh',title:'Fresh chat',backend:'code'}]});await new Promise(setImmediate);
  assert.equal(ui.view.rowsRoot.querySelector('.session').dataset.id,'fresh');
  await ui.view.choose('codex');assert.equal(ui.view.previewOnly,false);assert.equal(ui.view.container.hidden,true);
 }
});

test('search stays short in both languages while its accessible name stays specific',()=>{
 const ui=fixture();
 assert.equal(ui.view.search.placeholder,'搜索');
 assert.equal(ui.view.search.getAttribute('aria-label'),'搜索聊天或项目');
 ui.run("BridgeI18n.setLanguage('en');BridgeI18n.apply()");
 assert.equal(ui.view.search.placeholder,'Search');
 assert.equal(ui.view.search.dataset.i18nAriaLabel,'搜索聊天或项目');
 assert.equal(ui.run("BridgeI18n.t('搜索聊天或项目')"),'Search chats or projects');
});

test('session list explains a pending connection and offers retry only after timeout',async()=>{
 const ui=fixture(),client={id:'deepseek',enabled:true,configured:true,running:false,connected:false,connectionState:'starting',reason:'正在启动 Harness 桌面应用'},retries=[];
 ui.view.setClientStates([client],async id=>retries.push(id));ui.setHandler(async()=>{throw Error('客户端连接不可用，请在电脑端检查');});await ui.view.choose('deepseek');
 assert.match(ui.view.listStatus.textContent,/正在启动/);assert.doesNotMatch(ui.view.listStatus.textContent,/请在电脑端检查/);assert.equal(ui.view.listStatus.attributes['aria-busy'],'true');assert.equal(ui.view.connectionRetry.hidden,true);assert.equal(ui.calls.length,0);
 ui.view.setClientStates([{...client,connectionState:'timeout',retryable:true,reason:'Harness 连接超时，请检查桌面应用后重试接入'}],async id=>retries.push(id));await ui.view.refresh();assert.match(ui.view.listStatus.textContent,/连接超时/);assert.equal(ui.view.listStatus.attributes['aria-busy'],'false');assert.equal(ui.view.connectionRetry.hidden,false);await ui.view.connectionRetry.click();assert.deepEqual(retries,['deepseek']);
 ui.setHandler(async()=>({connected:true,sessions:[]}));await ui.view.setClientStates([{...client,connectionState:'connected',connected:true}]);assert.equal(ui.view.listStatus.textContent,'电脑上还没有聊天');assert.equal(ui.view.connectionRetry.hidden,true);
});

test('Claude session list exposes the confirmed initialization flow without running chat RPCs',async()=>{
 const ui=fixture(),client={id:'claude',name:'Claude',enabled:true,installed:true,connected:false,setupStatus:'needs-initialization'},initialized=[];
 ui.view.setClientStates([client],undefined,async id=>initialized.push(id));await ui.view.choose('claude');assert.match(ui.view.listStatus.textContent,/需要初始化连接/);assert.equal(ui.view.connectionInitialize.hidden,false);assert.equal(ui.calls.length,0);
 await ui.view.connectionInitialize.click();assert.deepEqual(initialized,['claude']);ui.run("BridgeI18n.setLanguage('en')");assert.match(ui.view.listStatus.textContent,/Connection initialization required/);assert.doesNotMatch(ui.view.listStatus.textContent,/[\u4e00-\u9fff]/);
 ui.view.setClientStates([{...client,connectionState:'connecting'}]);assert.equal(ui.view.connectionInitialize.hidden,true);await ui.view.refresh();assert.equal(ui.calls.length,0);
});

test('prepared Claude lists offer separate reconnect and reinitialization actions',async()=>{
 const ui=fixture(),client={id:'claude',name:'Claude',enabled:true,installed:true,connected:false,setupStatus:'needs-initialization',reconnectSupported:true,canReconnect:true},reconnects=[],initializations=[];
 ui.view.setClientStates([client],undefined,id=>initializations.push(id),undefined,id=>reconnects.push(id));await ui.view.choose('claude');
 assert.equal(ui.view.connectionReconnect.hidden,false);assert.equal(ui.view.connectionReconnect.textContent,'重新连接');assert.equal(ui.view.connectionInitialize.hidden,false);
 assert.equal(ui.view.connectionInitialize.textContent,'重新初始化连接');
 ui.run("BridgeI18n.setLanguage('en')");ui.view.renderConnectionStatus();assert.equal(ui.view.connectionReconnect.textContent,'Reconnect');ui.run("BridgeI18n.setLanguage('zh')");
 await ui.view.connectionReconnect.click();assert.deepEqual(reconnects,['claude']);assert.deepEqual(initializations,[]);
 await ui.view.connectionInitialize.click();assert.deepEqual(initializations,['claude']);
 ui.view.setClientStates([{...client,operationUncertain:true}],undefined,()=>{throw Error('unexpected initialize');},undefined,()=>{throw Error('unexpected reconnect');});
 assert.equal(ui.view.connectionReconnect.hidden,true);assert.equal(ui.view.connectionInitialize.hidden,true);
 ui.view.setClientStates([{...client,setupStatus:'owner-conflict',reason:'Claude 桥接连接被其他网关占用，请先停止旧网关；旧版连接还需重新加载 Claude'}]);
 assert.match(ui.view.listStatus.textContent,/其他网关占用/);assert.equal(ui.view.connectionReconnect.hidden,false);
});

test('unknown operations offer refresh only and a failed quit does not block connected chats',async()=>{
 const ui=fixture(),client={id:'claude',enabled:true,installed:true,connected:false,initializationMode:'background',setupStatus:'needs-unlock',connectionState:'needs-initialization',operationFailure:{action:'initialize',message:'timeout',uncertain:true}},refreshes=[];
 ui.view.setClientStates([client],()=>{throw Error('no replay');},()=>{throw Error('no initialization');},async()=>refreshes.push(true));await ui.view.choose('claude');assert.match(ui.view.listStatus.textContent,/电脑已锁定/);assert.match(ui.view.connectionNotice.textContent,/尚未确认/);assert.equal(ui.view.connectionInitialize.hidden,true);assert.equal(ui.view.connectionRetry.hidden,true);assert.equal(ui.view.connectionRefresh.hidden,false);await ui.view.connectionRefresh.click();assert.equal(refreshes.length,1);assert.equal(ui.calls.length,0);
 await ui.view.setClientStates([{...client,connected:true,connectionState:'connected',operationFailure:{action:'quit',message:'退出未完成',uncertain:false}}]);assert.ok(ui.calls.some(call=>call.url.includes('/list')));assert.equal(ui.view.listStatus.textContent,'电脑上还没有聊天');assert.equal(ui.view.connectionNotice.textContent,'退出未完成');assert.equal(ui.view.connectionInitialize.hidden,true);
});

test('session list surfaces genuine failures outside startup and never claims disconnected data is connected',async()=>{
 const ui=fixture();ui.view.setClientStates([{id:'deepseek',enabled:true,connected:false,connectionState:'idle'}]);ui.setHandler(async()=>{throw Error('real failure');});await ui.view.choose('deepseek');assert.equal(ui.view.listStatus.textContent,'real failure');
 ui.setHandler(async()=>({connected:false,sessions:[]}));await ui.view.refresh();assert.equal(ui.view.listStatus.textContent,'尚未连接');
});
test('desktop client keeps cached rows through brief polling failures and clears sustained failure after recovery',async()=>{
 const ui=fixture();ui.setHandler(async()=>({sessions:[{id:'a',title:'Saved chat',backend:'code'}]}));await ui.view.choose('claude');
 ui.setHandler(async()=>{throw Error('network unavailable');});
 for(let attempt=1;attempt<=3;attempt++){await assert.rejects(ui.view.refreshList(),/network unavailable/);assert.match(ui.view.rowsRoot.textContent,/Saved chat/);assert.equal(ui.view.listStatus.textContent,'');assert.equal(ui.view.listHint.hidden,attempt<3);}
 ui.setHandler(async()=>({sessions:[{id:'a',title:'Current chat',backend:'code'}]}));await ui.view.refreshList();assert.match(ui.view.rowsRoot.textContent,/Current chat/);
});
test('LAN HTTP send creates a secure id and retries the same unconfirmed operation',async()=>{
 const ui=await opened();ui.run('uuid=undefined');let attempt=0;ui.setHandler(async(url,body)=>{if(body){if(++attempt===1)throw Error('connection lost');return {status:'accepted'};}return url.includes('/detail')?{session:{id:'a',title:'Chat',status:'idle'},messages:[]}:{sessions:[]};});
 ui.view.input.value='hello';await ui.view.submit();assert.equal(ui.view.input.value,'hello');await ui.view.detail(ui.view.generation);await ui.view.submit();const writes=ui.calls.filter(c=>c.body);assert.equal(writes.length,2);assert.match(writes[0].body.id,/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/);assert.equal(writes[0].body.id,writes[1].body.id);assert.equal(ui.view.input.value,'');
});
test('unknown desktop send keeps its ID when account identity arrives before manual retry',async()=>{
 const ui=await opened();ui.view.bindDraftGateway('gateway-a');let writes=0;
 ui.setHandler(async(url,body)=>body?{status:++writes===1?'unknown':'accepted'}:url.includes('/detail')?{session:{id:'a',title:'Chat',status:'idle'},draftAccountIdentity:'account-a',messages:[]}:{sessions:[]});
 ui.view.input.value='same message';ui.view.input.oninput();await ui.view.submit();const first=ui.calls.filter(c=>c.body)[0].body.id;
 await ui.view.detail(ui.view.generation);ui.view.bindDraftScope({draftAccountIdentity:'account-a'});await ui.view.submit();
 const sent=ui.calls.filter(c=>c.body);assert.equal(sent.length,2);assert.equal(sent[1].body.id,first);assert.equal(ui.view.composerDrafts.pending({gateway:'gateway-a',provider:'claude',account:'account-a',host:'local',session:'a'}),null);
});
test('desktop storage write failure keeps the uncertain ID when storage recovers',async()=>{
 const ui=await opened();ui.view.bindDraftGateway('gateway-a');ui.view.bindDraftScope({draftAccountIdentity:'account-a'});
 const original=ui.view.composerDrafts.storage;let fail=true,writes=0;
 ui.view.composerDrafts.storage={getItem:key=>original.getItem(key),removeItem:key=>original.removeItem(key),setItem:(key,value)=>{if(fail)throw Error('quota');original.setItem(key,value);}};
 ui.setHandler(async(url,body)=>body?{status:++writes===1?'unknown':'accepted'}:url.includes('/detail')?{session:{id:'a',title:'Chat',status:'idle'},draftAccountIdentity:'account-a',messages:[]}:{sessions:[]});
 ui.view.input.value='same message';ui.view.input.oninput();await ui.view.submit();const first=ui.calls.filter(c=>c.body)[0].body.id;
 assert.equal(ui.view.provisionalDrafts.get('claude:a').text,'same message');
 fail=false;await ui.view.detail(ui.view.generation);await ui.view.submit();
 const sent=ui.calls.filter(c=>c.body);assert.equal(sent.length,2);assert.equal(sent[1].body.id,first);
});
test('confirmed desktop send clears the provisional draft while an unknown result keeps it',async()=>{
 const ui=await opened();ui.view.bindDraftGateway('gateway-a');ui.view.bindDraftScope({draftAccountIdentity:'account-a'});
 ui.view.composerDrafts.storage={getItem(){return null;},setItem(){throw Error('quota');},removeItem(){}};
 let writes=0;ui.setHandler(async(url,body)=>body?{status:++writes===1?'unknown':'accepted'}:url.includes('/detail')?{session:{id:'a',title:'Chat',status:'idle'},draftAccountIdentity:'account-a',messages:[]}:{sessions:[]});
 ui.view.input.value='same message';ui.view.input.oninput();await ui.view.submit();assert.equal(ui.view.provisionalDrafts.get('claude:a').text,'same message');
 await ui.view.detail(ui.view.generation);await ui.view.submit();assert.equal(writes,2);assert.equal(ui.view.provisionalDrafts.has('claude:a'),false);assert.equal(ui.view.drafts.has('claude:a'),false);
});
test('Claude A/B/A open path waits for account identity before restoring provisional drafts',async()=>{
 const ui=fixture();ui.view.bindDraftGateway('gateway-a');await ui.view.choose('claude');const waiting=new Map();
 ui.setHandler((url)=>url.includes('/detail')?new Promise(resolve=>waiting.set(new URL(url,'https://gateway.test').searchParams.get('sessionId'),resolve)):Promise.resolve({sessions:[]}));
 const firstA=ui.view.open('a');ui.view.input.value='draft A';ui.view.input.oninput();ui.view.backToList();
 const firstB=ui.view.open('b');ui.view.input.value='draft B';ui.view.input.oninput();ui.view.backToList();
 const secondA=ui.view.open('a');assert.equal(ui.view.input.value,'');assert.equal(ui.view.provisionalDrafts.get('claude:a').text,'draft A');
 waiting.get('a')({session:{id:'a',title:'A',status:'idle'},draftAccountIdentity:'account-a',messages:[]});await Promise.all([firstA,secondA]);
 assert.equal(ui.view.input.value,'draft A');assert.equal(ui.view.provisionalDrafts.has('claude:a'),false);
 const secondB=ui.view.open('b');assert.equal(ui.view.input.value,'');
 waiting.get('b')({session:{id:'b',title:'B',status:'idle'},draftAccountIdentity:'account-a',messages:[]});await Promise.all([firstB,secondB]);
 assert.equal(ui.view.input.value,'draft B');
});
test('provisional desktop draft cannot cross a verified account or gateway change',async()=>{
 const ui=await opened();ui.view.bindDraftGateway('gateway-a');
 ui.view.provisionalDrafts.set('claude:a',{gateway:'gateway-a',account:'account-old',text:'secret',sendMode:'send',skills:[]});
 ui.view.drafts.set('claude:a','secret');ui.sessionStore.set('desktop-draft:claude:a','secret');
 ui.view.prepareChat('a');ui.view.bindDraftScope({draftAccountIdentity:'account-new'});
 assert.equal(ui.view.input.value,'');assert.equal(ui.view.provisionalDrafts.has('claude:a'),false);assert.equal(ui.view.drafts.has('claude:a'),false);assert.equal(ui.sessionStore.has('desktop-draft:claude:a'),false);
 assert.notEqual(ui.view.composerDrafts.load({gateway:'gateway-a',provider:'claude',account:'account-new',host:'local',session:'a'})?.text,'secret');
 ui.view.provisionalDrafts.set('claude:a',{gateway:'gateway-a',account:'',text:'pending'});ui.sessionStore.set('desktop-draft:claude:a','pending');ui.view.bindDraftGateway('gateway-b');
 assert.equal(ui.view.provisionalDrafts.size,0);assert.equal(ui.sessionStore.has('desktop-draft:claude:a'),false);
 ui.view.provisionalDrafts.set('claude:a',{gateway:'gateway-b',account:'',text:'logout'});ui.sessionStore.set('desktop-draft:claude:a','logout');ui.view.clear();
 assert.equal(ui.view.provisionalDrafts.size,0);assert.equal(ui.sessionStore.has('desktop-draft:claude:a'),false);
});
test('client shell and welcome appear before the first list response',async()=>{
 const ui=fixture();let finish;ui.setHandler(()=>new Promise(resolve=>finish=resolve));const reading=ui.view.choose('claude');assert.equal(ui.view.container.hidden,false);assert.equal(ui.view.welcome.hidden,false);assert.match(ui.view.welcome.textContent,/从一条聊天继续/);assert.equal(ui.view.conversation.hidden,true);assert.match(ui.view.listStatus.textContent,/读取|加载|连接/);finish({sessions:[]});await reading;
});
test('returning to list clears selection and prevents an old detail from reopening chat',async()=>{
 const ui=await opened();ui.view.input.value='draft';let finish;ui.setHandler(()=>new Promise(resolve=>finish=resolve));const reading=ui.view.open('b');assert.equal(ui.view.isChatOpen,true);ui.view.backToList();assert.equal(ui.view.sid,null);assert.equal(ui.view.isChatOpen,false);assert.equal(ui.view.welcome.hidden,false);finish({session:{id:'b',title:'late',status:'idle'},messages:[]});await reading;assert.equal(ui.view.sid,null);assert.equal(ui.view.conversation.hidden,true);assert.equal(ui.view.drafts.get('claude:a'),'draft');
});
test('composer and historical replies show their own model and effort without rewriting history',async()=>{
 const ui=fixture();await ui.view.choose('claude');ui.setHandler(async()=>({session:{id:'a',title:'Chat',model:'new-model',effort:'high',status:'idle',capabilities:{}},messages:[{id:'1',role:'assistant',model:'old-model',effort:'low',text:'answer'},{id:'2',role:'assistant',text:'no metadata'}]}));await ui.view.open('a');assert.match(ui.view.modelButton.textContent,/new-model/);assert.match(ui.view.modelButton.textContent,/high/);assert.match(ui.view.messageList.children[0].textContent,/old-model.*low/);assert.doesNotMatch(ui.view.messageList.children[1].textContent,/new-model|high/);assert.ok(ui.view.modelButton.classList.contains('tool-button'));assert.equal(ui.view.input.rows,1);assert.equal(ui.view.composerToggle,undefined);assert.equal(ui.view.composerBody.hidden,false);
});
test('Claude recent list combines types chronologically and retains archive, search and project filters',async()=>{
 const ui=fixture();await ui.view.choose('claude');ui.view.rows=[{id:'a',title:'Code chat',backend:'code',cwd:'/same',updatedAt:1},{id:'b',title:'Cowork chat',backend:'cowork',cwd:'/same',updatedAt:2},{id:'c',title:'Archive',backend:'code',cwd:'/same',archived:true}];ui.view.renderList();
 assert.deepEqual(ui.view.rowsRoot.querySelectorAll('.session').map(n=>n.dataset.id),['b','a']);assert.equal(ui.view.rowsRoot.querySelector('details'),null);assert.equal(ui.view.surface.hidden,false);assert.deepEqual(ui.view.surface.children.map(n=>n.value),['all','code','cowork']);assert.doesNotMatch(ui.view.rowsRoot.textContent,/Archive/);
 ui.view.search.value='Cowork';ui.view.renderList();assert.match(ui.view.rowsRoot.textContent,/Cowork chat/);assert.doesNotMatch(ui.view.rowsRoot.textContent,/Code chat/);
 ui.view.search.value='';ui.view.archived.checked=true;ui.view.listMode.value='project';ui.view.renderList();assert.match(ui.view.rowsRoot.textContent,/Archive/);assert.equal(ui.view.rowsRoot.querySelectorAll('details').length,1);assert.equal(ui.view.rowsRoot.querySelector('summary').textContent,'/same');
});
test('Claude recent list compares Desktop milliseconds and CLI seconds as the same time scale',async()=>{
 const ui=fixture();await ui.view.choose('claude');
 const desktopOlder=Date.UTC(2026,9,14,12),cliNewest=Date.UTC(2026,9,16,12),desktopMiddle=Date.UTC(2026,9,15,12);
 ui.view.rows=[{id:'code',title:'Desktop Code',backend:'code',cwd:'/same',updatedAt:desktopOlder},
  {id:'cli',title:'CLI latest',backend:'cli',cwd:'/same',updatedAt:cliNewest/1000},
  {id:'cowork',title:'Desktop Cowork',backend:'cowork',cwd:'/same',updatedAt:desktopMiddle}];
 ui.view.renderList();
 const ids=()=>ui.view.rowsRoot.querySelectorAll('.session').map(row=>row.dataset.id);
 assert.deepEqual(ids(),['cli','cowork','code']);
 assert.equal(ui.view.rowsRoot.querySelector('.session').querySelector('time').textContent,
  new Date(cliNewest).toLocaleDateString('zh-CN',{month:'numeric',day:'numeric'}));
 ui.view.surface.value='cli';ui.view.surface.onchange();assert.deepEqual(ids(),['cli']);
 ui.view.surface.value='all';ui.view.surface.onchange();ui.view.search.value='Desktop';ui.view.renderList();assert.deepEqual(ids(),['cowork','code']);
 ui.view.search.value='';ui.view.listMode.value='project';ui.view.renderList();assert.deepEqual(ids(),['cli','cowork','code']);
});
test('Claude type filter uses full inventory and returns to all when a type disappears',async()=>{
 const ui=fixture();await ui.view.choose('claude');ui.view.rows=[{id:'code',title:'Code chat',backend:'code',updatedAt:2},{id:'cli',title:'CLI chat',backend:'cli',updatedAt:1}];ui.view.renderList();const selector=ui.view.surface;assert.equal(selector.hidden,false);assert.deepEqual(selector.children.map(n=>n.value),['all','code','cli']);assert.equal(selector.children[0].textContent,'全部会话');assert.deepEqual(ui.view.rowsRoot.querySelectorAll('.session').map(n=>n.dataset.id),['code','cli']);
 selector.value='cli';selector.onchange();assert.deepEqual(ui.view.rowsRoot.querySelectorAll('.session').map(n=>n.dataset.id),['cli']);const options=[...selector.children];ui.view.rows=[{id:'code',title:'Code updated',backend:'code',updatedAt:3},{id:'cli',title:'CLI chat',backend:'cli',updatedAt:1}];ui.view.renderList();assert.strictEqual(selector.children[0],options[0]);assert.strictEqual(selector.children[2],options[2]);ui.view.search.value='Code';ui.view.renderList();assert.strictEqual(selector.children[2],options[2]);assert.equal(selector.hidden,false);assert.equal(selector.value,'cli');ui.run("BridgeI18n.setLanguage('en')");ui.view.relabel();assert.equal(selector.children[0].textContent,'All');assert.equal(selector.value,'cli');ui.view.search.value='';ui.view.rows=[{id:'code',title:'Code chat',backend:'code',updatedAt:2}];ui.view.renderList();assert.equal(selector.hidden,true);assert.equal(selector.value,'all');assert.equal(ui.view.rowsRoot.querySelector('.ds-session-type').textContent,'Code');assert.equal(ui.view.rowsRoot.querySelector('.session').classList.contains('ds-session-row'),true);
});
test('single-source Claude and DSH rows retain their type label above the date',()=>{
 const ui=fixture();
 for(const [provider,backend,label] of [['claude','code','Code'],['claude','cowork','Cowork'],['claude','cli','CLI'],['deepseek','chat','APP']]){
  ui.view.provider=provider;ui.view.rows=[{id:'single',backend,title:'Only source',updatedAt:1}];ui.view.renderList();
  const row=ui.view.rowsRoot.querySelector('.session');
  assert.equal(ui.view.surface.hidden,true);assert.ok(row.classList.contains('ds-session-row'));
  assert.equal(row.children[1].className,'ds-session-type');assert.equal(row.children[1].textContent,label);
  assert.equal(row.children[2].tagName,'SMALL');assert.ok(row.children[2].querySelector('time'));
 }
});
test('Claude type choice survives provider switches but not loss of that type',async()=>{
 const ui=fixture();ui.setHandler(async url=>({sessions:url.includes('/claude/')?[{id:'code',title:'Code',backend:'code'},{id:'cowork',title:'Cowork',backend:'cowork'}]:[]}));await ui.view.choose('claude');ui.view.surface.value='cowork';ui.view.surface.onchange();await ui.view.choose('deepseek');assert.equal(ui.view.surface.hidden,true);await ui.view.choose('claude');assert.equal(ui.view.surface.value,'cowork');assert.deepEqual(ui.view.rowsRoot.querySelectorAll('.session').map(n=>n.dataset.id),['cowork']);
});
test('workspace tabs use the real Workbench provider routes and split behavior',async()=>{
 const ui=await opened();assert.equal(ui.view.workbench.current.provider,'claude');ui.view.workbench.files();await Promise.resolve();assert.ok(ui.calls.some(c=>c.url.includes('/api/desktop-sessions/claude/workspace?sessionId=a')));ui.view.workbench.split('files');assert.equal(ui.view.workbench.splitMode,true);assert.ok(ui.view.conversation.classList.contains('wb-split'));assert.equal(ui.view.workbench.current.splitTab,'files');
});
test('PDF reading position survives file-tab and chat-back navigation',async()=>{
 const ui=await opened(),wb=ui.view.workbench,session=wb.current;
 const files={id:'files',name:'Files',body:wb.node('div','wb-browser')};
 const pdf={id:'file:report.pdf',name:'report.pdf',body:wb.node('div','wb-preview')};
 const stage=wb.node('div','wb-pdf-stage');pdf.body.append(stage);session.files=[files,pdf];
 wb.select(pdf.id);stage.scrollTop=720;stage.scrollLeft=42;
 wb.select(files.id);stage.scrollTop=0;stage.scrollLeft=0;
 wb.select(pdf.id);assert.equal(stage.scrollTop,720);assert.equal(stage.scrollLeft,42);
 stage.scrollTop=915;stage.scrollLeft=19;
 assert.equal(wb.back(),true);assert.equal(session.active,'chat');
 stage.scrollTop=0;stage.scrollLeft=0;
 wb.select(pdf.id);assert.equal(stage.scrollTop,915);assert.equal(stage.scrollLeft,19);
});
test('header back returns Claude and DeepSeek directly to their list without changing file directory or draft',async()=>{
 for(const provider of ['claude','deepseek']){
  const ui=fixture();await ui.view.choose(provider);await ui.view.open('a');const wb=ui.view.workbench,session=wb.current;
  const settle=()=>new Promise(setImmediate),entries={'':[{name:'src',path:'src',kind:'directory'}],src:[{name:'lib',path:'src/lib',kind:'directory'}],'src/lib':[]};
  ui.setHandler(async url=>{const at=new URL(url,'http://fixture').searchParams.get('path');return {project:'/project',total:entries[at]?.length||0,entries:entries[at]||[],nextOffset:null};});
  wb.files();await settle();const tab=session.files.find(t=>t.id==='files');
  tab.list.querySelector('.wb-file').click();await settle();tab.list.querySelector('.wb-file').click();await settle();
  ui.view.input.value='Keep my draft';ui.view.input.oninput();tab.list.scrollTop=120;
  const calls=ui.calls.length;wb.locateState={session,tab,row:tab.list};
  await ui.view.back.click();
  assert.equal(ui.view.isChatOpen,false);assert.equal(session.directory,'src/lib');assert.equal(session.active,'files');assert.equal(session.files[0],tab);
  assert.equal(tab.list.scrollTop,120);assert.equal(ui.view.drafts.get(provider+':a'),'Keep my draft');assert.equal(wb.locateState,null);assert.equal(ui.calls.length,calls);
  await ui.view.open('a');assert.equal(wb.current,session);assert.equal(session.directory,'src/lib');assert.equal(session.active,'files');assert.equal(ui.view.input.value,'Keep my draft');
  assert.equal(wb.back(),true);assert.equal(session.directory,'src');assert.equal(ui.view.isChatOpen,true);
 }
});
test('rapid file backs ignore stale responses and a failed parent read stays in the file browser',async()=>{
 const ui=await opened(),wb=ui.view.workbench,session=wb.current,pending=[];
 ui.setHandler((url,_body)=>{const path=new URL(url,'http://fixture').searchParams.get('path');if(path==='')return Promise.resolve({project:'/project',total:0,entries:[],nextOffset:null});return new Promise((resolve,reject)=>pending.push({path,resolve,reject}));});
 wb.files();await Promise.resolve();const tab=session.files.find(t=>t.id==='files');
 const deep=wb.directory(session,tab,'src/lib');assert.equal(wb.back(),true);assert.equal(session.directory,'src');
 const parent=pending.at(-1);assert.equal(parent.path,'src');assert.equal(wb.back(),true);assert.equal(session.directory,'');
 parent.reject(Error('late parent failure'));pending[0].resolve({project:'/old',total:1,entries:[{name:'old',path:'old',kind:'file'}],nextOffset:null});
 await deep;await Promise.resolve();assert.equal(session.directory,'');assert.equal(tab.status.classList.contains('error'),false);assert.equal(tab.list.children.length,0);
 const failed=wb.directory(session,tab,'src');pending.at(-1).reject(Error('directory unavailable'));await failed;
 assert.equal(session.active,'files');assert.equal(session.directory,'src');assert.equal(tab.status.classList.contains('error'),true);
 assert.equal(wb.back(),true);await Promise.resolve();assert.equal(session.directory,'');assert.equal(session.active,'files');
});
test('a directory response from an earlier chat cannot replace the selected chat file view',async()=>{
 const ui=await opened(),wb=ui.view.workbench,first=wb.current;let finish;
 ui.setHandler(url=>new Promise(resolve=>{const path=new URL(url,'http://fixture').searchParams.get('path');if(path==='src')finish=resolve;else resolve({project:'/second',total:0,entries:[],nextOffset:null});}));
 wb.files();await Promise.resolve();const oldTab=first.files.find(t=>t.id==='files'),reading=wb.directory(first,oldTab,'src');
 wb.open('b','local');wb.files();await Promise.resolve();const second=wb.current,secondTab=second.files.find(t=>t.id==='files');
 finish({project:'/first',total:1,entries:[{name:'old',path:'src/old',kind:'file'}],nextOffset:null});await reading;
 assert.equal(wb.current,second);assert.equal(second.directory,'');assert.equal(secondTab.list.children.length,0);assert.equal(wb.panel.firstElementChild,secondTab.body);
});
test('file locate loads later pages and centers only the file list before focusing the row',async()=>{
 const ui=await opened(),wb=ui.view.workbench,session=wb.current;let finish,focusOptions;
 ui.run('globalThis.frames=[];requestAnimationFrame=fn=>{frames.push(fn);return frames.length};cancelAnimationFrame=()=>{}');
 ui.setHandler(url=>{const offset=Number(new URL(url,'http://fixture').searchParams.get('offset'));return offset?new Promise(resolve=>finish=resolve):Promise.resolve({project:'/project',total:2,entries:[{name:'first.txt',path:'src/first.txt',kind:'file',size:1}],nextOffset:1});});
 const original=wb.button.bind(wb);wb.button=(text,action,title)=>{const button=original(text,action,title);if(title==='late.txt'){button.getBoundingClientRect=()=>({top:700,bottom:750});button.focus=options=>{focusOptions=options;ui.document.activeElement=button;};}return button;};
 const locating=wb.locate(session,'src/late.txt');await new Promise(setImmediate);
 const tab=session.files.find(t=>t.id==='files'),list=tab.list;Object.assign(list,{clientHeight:200,scrollHeight:1000,scrollTop:0});list.getBoundingClientRect=()=>({top:100,bottom:300});
 const timeline=ui.view.messages,timelineTop=timeline.scrollTop=33;
 finish({project:'/project',total:2,entries:[{name:'late.txt',path:'src/late.txt',kind:'file',size:1}],nextOffset:null});await locating;
 const row=list.children[1];assert.notEqual(ui.document.activeElement,row);assert.equal(ui.run('frames.length'),1);
 ui.run('frames.shift()(100)');assert.equal(list.scrollTop,0);ui.run('frames.shift()(250)');assert.ok(list.scrollTop>0&&list.scrollTop<525);
 ui.run('frames.shift()(400)');assert.equal(list.scrollTop,525);assert.equal(timeline.scrollTop,timelineTop);assert.equal(ui.document.activeElement,row);
 assert.equal(focusOptions.preventScroll,true);assert.equal(row.classList.contains('wb-locate-highlight'),true);
});
test('visible file locate leaves scroll untouched and repeated folder locate restarts its highlight',async()=>{
 const ui=await opened(),wb=ui.view.workbench,session=wb.current;
 ui.run('globalThis.frames=[];globalThis.timers=[];globalThis.cleared=[];requestAnimationFrame=fn=>{frames.push(fn);return frames.length};setTimeout=(fn,ms)=>{timers.push({fn,ms});return timers.length};clearTimeout=id=>cleared.push(id)');
 ui.setHandler(async()=>({project:'/project',total:1,entries:[{name:'docs',path:'src/docs',kind:'directory'}],nextOffset:null}));
 await wb.locate(session,'src/docs');const tab=session.files.find(t=>t.id==='files'),first=tab.list.children[0];
 assert.equal(ui.run('frames.length'),0);assert.equal(tab.list.scrollTop,0);assert.equal(ui.document.activeElement,first);
 assert.equal(first.classList.contains('wb-locate-highlight'),true);assert.equal(ui.run('timers.at(-1).ms'),3400);
 await wb.locate(session,'src/docs');const second=tab.list.children[0];
 assert.equal(second,first);assert.equal(ui.run('cleared.length'),1);assert.equal(ui.run('timers.length'),2);
 assert.equal(second.classList.contains('wb-locate-highlight'),true);assert.equal(ui.document.activeElement,second);
});
test('locating a fully visible row deep in an existing directory preserves its node and scroll',async()=>{
 const ui=await opened(),wb=ui.view.workbench,session=wb.current;session.directory='src';
 const entries=Array.from({length:30},(_,index)=>({name:`item-${index}.txt`,path:`src/item-${index}.txt`,kind:'file',size:1}));
 ui.setHandler(async()=>({project:'/project',total:entries.length,entries,nextOffset:null}));
 wb.files();await new Promise(setImmediate);const tab=session.files.find(t=>t.id==='files'),list=tab.list,row=list.children[22],calls=ui.calls.length;
 Object.assign(list,{clientHeight:200,scrollHeight:1800,scrollTop:1180});list.getBoundingClientRect=()=>({top:100,bottom:300});row.getBoundingClientRect=()=>({top:155,bottom:205});
 await wb.locate(session,row.dataset.path);
 assert.equal(ui.calls.length,calls);assert.equal(tab.list,list);assert.equal(list.children[22],row);assert.equal(list.scrollTop,1180);
 assert.equal(session.hidden,false);assert.equal(tab.body.querySelectorAll('input')[1].checked,false);
 assert.equal(ui.document.activeElement,row);assert.equal(row.classList.contains('wb-locate-highlight'),true);
 assert.equal(list.onwheel,null);assert.equal(list.ontouchstart,null);assert.equal(list.onpointerdown,null);
});
test('reduced motion locates offscreen files instantly and holds the cue for three seconds',async()=>{
 const ui=await opened(),wb=ui.view.workbench,session=wb.current;
 ui.run('window.matchMedia=()=>({matches:true});globalThis.frames=[];globalThis.timers=[];requestAnimationFrame=fn=>{frames.push(fn);return frames.length};setTimeout=(fn,ms)=>{timers.push({fn,ms});return timers.length}');
 ui.setHandler(async()=>({project:'/project',total:1,entries:[{name:'far.txt',path:'src/far.txt',kind:'file',size:1}],nextOffset:null}));
 const original=wb.node.bind(wb);wb.node=(tag,cls,text)=>{const node=original(tag,cls,text);if(cls==='wb-file-list'){Object.assign(node,{clientHeight:200,scrollHeight:1000});node.getBoundingClientRect=()=>({top:100,bottom:300});}return node;};
 const button=wb.button.bind(wb);wb.button=(text,action,title)=>{const node=button(text,action,title);if(title==='far.txt')node.getBoundingClientRect=()=>({top:700,bottom:750});return node;};
 await wb.locate(session,'src/far.txt');const tab=session.files.find(t=>t.id==='files'),row=tab.list.children[0];
 assert.equal(tab.list.scrollTop,525);assert.equal(ui.run('frames.length'),0);assert.equal(ui.document.activeElement,row);
 assert.equal(ui.run('timers.at(-1).ms'),3000);assert.equal(row.classList.contains('wb-locate-highlight'),true);
 ui.run('timers.at(-1).fn()');assert.equal(row.classList.contains('wb-locate-highlight'),false);
});
test('manual input cancels locate motion and old pagination cannot displace a newer target',async()=>{
 const ui=await opened(),wb=ui.view.workbench,session=wb.current;let oldPage;
 ui.run('globalThis.frames=[];globalThis.cancelled=[];requestAnimationFrame=fn=>{frames.push(fn);return frames.length};cancelAnimationFrame=id=>cancelled.push(id)');
 let offsetReads=0;ui.setHandler(url=>{const offset=Number(new URL(url,'http://fixture').searchParams.get('offset'));
   if(offset&&++offsetReads===1)return new Promise(resolve=>oldPage=resolve);
   if(offset)return Promise.resolve({project:'/project',total:1,entries:[{name:'new.txt',path:'src/new.txt',kind:'file',size:1}],nextOffset:null});
   return Promise.resolve({project:'/project',total:2,entries:[],nextOffset:1});});
 const stale=wb.locate(session,'src/old.txt');await new Promise(setImmediate);assert.equal(typeof oldPage,'function');
 await wb.locate(session,'src/new.txt');const tab=session.files.find(t=>t.id==='files'),newRow=tab.list.children[0];
 oldPage({project:'/old',total:2,entries:[{name:'old.txt',path:'src/old.txt',kind:'file',size:1}],nextOffset:null});await stale;
 assert.equal(tab.list.children.length,1);assert.equal(tab.list.children[0],newRow);assert.equal(ui.document.activeElement,newRow);
 const node=wb.node.bind(wb);wb.node=(tag,cls,text)=>{const result=node(tag,cls,text);if(cls==='wb-file-list'){Object.assign(result,{clientHeight:200,scrollHeight:1000});result.getBoundingClientRect=()=>({top:100,bottom:300});}return result;};
 const original=wb.button.bind(wb);wb.button=(text,action,title)=>{const row=original(text,action,title);if(title==='moving.txt')row.getBoundingClientRect=()=>({top:700,bottom:750});return row;};
 ui.setHandler(async()=>({project:'/project',total:1,entries:[{name:'moving.txt',path:'other/moving.txt',kind:'file',size:1}],nextOffset:null}));
 const locating=wb.locate(session,'other/moving.txt');await locating;const list=tab.list;
 assert.equal(ui.run('frames.length'),1);const before=list.scrollTop;
 list.onwheel({type:'wheel'});assert.equal(ui.run('cancelled.length'),1);assert.equal(list.scrollTop,before);
 ui.run('frames.shift()(400)');assert.equal(list.scrollTop,before);assert.equal(ui.document.activeElement,newRow);
 wb.select('chat');assert.equal(session.active,'chat');
});
test('manual scroll during pagination and direct split switching discard pending locate',async()=>{
 const ui=await opened(),wb=ui.view.workbench,session=wb.current;let finish;
 ui.setHandler(url=>Number(new URL(url,'http://fixture').searchParams.get('offset'))
   ?new Promise(resolve=>finish=resolve)
   :Promise.resolve({project:'/project',total:1,entries:[],nextOffset:1}));
 const first=wb.locate(session,'src/target.txt');await new Promise(setImmediate);
 const tab=session.files.find(t=>t.id==='files'),list=tab.list;assert.equal(typeof list.onwheel,'function');
 list.onwheel({type:'wheel'});assert.equal(list.onwheel,null);
 finish({project:'/project',total:1,entries:[{name:'target.txt',path:'src/target.txt',kind:'file',size:1}],nextOffset:null});await first;
 assert.equal(list.children.length,1);assert.notEqual(ui.document.activeElement,list.children[0]);
 const second=wb.locate(session,'other/target.txt');await new Promise(setImmediate);
 const terminal={id:'terminal',name:'Terminal',body:wb.node('div','wb-terminal')};session.files.push(terminal);wb.split('terminal');
 assert.equal(session.active,'terminal');assert.equal(tab.list.onwheel,null);
 finish({project:'/project',total:1,entries:[{name:'target.txt',path:'other/target.txt',kind:'file',size:1}],nextOffset:null});await second;
 assert.equal(tab.list.children.length,1);assert.notEqual(ui.document.activeElement,tab.list.children[0]);
});
test('touch during initial locate load still renders the directory without continuing locate',async()=>{
 const ui=await opened(),wb=ui.view.workbench,session=wb.current;let finish;
 ui.setHandler(()=>new Promise(resolve=>finish=resolve));
 const locating=wb.locate(session,'src/target.txt'),tab=session.files.find(t=>t.id==='files'),list=tab.list;
 assert.equal(typeof list.ontouchstart,'function');list.ontouchstart({type:'touchstart'});
 assert.equal(list.ontouchstart,null);
 finish({project:'/project',total:1,entries:[{name:'target.txt',path:'src/target.txt',kind:'file',size:1}],nextOffset:null});await locating;
 assert.equal(session.directory,'src');assert.equal(list.children.length,1);assert.equal(tab.loaded,true);
 assert.notEqual(ui.document.activeElement,list.children[0]);assert.equal(list.scrollTop,0);
 const pending=new Map();ui.setHandler(url=>new Promise(resolve=>pending.set(new URL(url,'http://fixture').searchParams.get('path'),resolve)));
 const stale=wb.locate(session,'other/stale.txt'),current=wb.directory(session,tab,'manual');
 pending.get('manual')({project:'/project',total:1,entries:[{name:'current.txt',path:'manual/current.txt',kind:'file',size:1}],nextOffset:null});await current;
 pending.get('other')({project:'/old',total:1,entries:[{name:'stale.txt',path:'other/stale.txt',kind:'file',size:1}],nextOffset:null});await stale;
 assert.equal(session.directory,'manual');assert.equal(tab.list.children.length,1);assert.equal(tab.list.children[0].dataset.path,'manual/current.txt');
});
test('wide split file back consumes the visible file tab, then returns to unsplit chat',async()=>{
 const ui=await opened(),wb=ui.view.workbench,session=wb.current;
 ui.setHandler(async()=>({project:'/project',total:0,entries:[],nextOffset:null}));wb.files();await Promise.resolve();const tab=session.files.find(t=>t.id==='files');
 wb.split('files');await wb.directory(session,tab,'src/lib');assert.equal(wb.isFileVisible,false);
 assert.equal(wb.back(),true);assert.equal(session.directory,'src');assert.equal(wb.splitMode,true);
 assert.equal(wb.back(),true);assert.equal(session.directory,'');assert.equal(wb.splitMode,true);
 assert.equal(wb.back(),true);assert.equal(session.active,'chat');assert.equal(session.splitTab,null);assert.equal(wb.splitMode,false);
 assert.equal(wb.back(),false);
});

for(const [closed,next] of [['files','terminal'],['terminal','file:preview'],['file:preview','terminal']])test(`closing active split ${closed} selects adjacent ${next} and preserves the main chat`,async()=>{
 const ui=await opened(),wb=ui.view.workbench,session=wb.current,disposed=[];
 session.files=['files','terminal','file:preview'].map(id=>({id,name:id,body:wb.node('div'),terminal:id==='terminal'?{dispose:()=>disposed.push(id)}:undefined}));
 ui.view.input.value='unsent draft';ui.view.messages.scrollTop=42;wb.split(closed);session.splitRatio=.6;
 await wb.close(closed);assert.equal(session.splitTab,next);assert.equal(session.active,next);assert.equal(wb.splitMode,true);assert.equal(wb.panel.firstElementChild,session.files.find(t=>t.id===next).body);assert.equal(ui.view.input.value,'unsent draft');assert.equal(ui.view.messages.scrollTop,42);assert.equal(session.splitRatio,.6);assert.deepEqual(disposed,closed==='terminal'?['terminal']:[]);
});
test('inactive closes and consecutive closes preserve split until the final tool is closed',async()=>{
 const ui=await opened(),wb=ui.view.workbench,s=wb.current;
 s.files=['files','terminal','file:preview'].map(id=>({id,name:id,body:wb.node('div')}));wb.split('terminal');
 await wb.close('files');assert.equal(s.splitTab,'terminal');await wb.close('terminal');assert.equal(s.splitTab,'file:preview');assert.equal(wb.splitMode,true);
 await wb.close('file:preview');assert.equal(s.splitTab,null);assert.equal(s.active,'chat');assert.equal(wb.splitMode,false);assert.equal(wb.panel.hidden,true);
});
test('closing a split tool while narrow preserves the adjacent tool when widened',async()=>{
 const ui=await opened(),wb=ui.view.workbench,s=wb.current;
 s.files=['files','file:preview'].map(id=>({id,name:id,body:wb.node('div')}));wb.split('file:preview');ui.view.conversation.clientWidth=390;wb.paint();
 await wb.close('file:preview');assert.equal(s.splitTab,'files');assert.equal(wb.splitMode,false);ui.view.conversation.clientWidth=1000;wb.paint();assert.equal(wb.splitMode,true);assert.equal(wb.panel.firstElementChild,s.files[0].body);
 wb.unsplit();await wb.close('files');assert.equal(s.active,'chat');assert.equal(s.splitTab,null);
});
test('closing a side-chat tab preserves the native chat and updates only its original session',async()=>{
 const ui=await opened(),wb=ui.view.workbench,s=wb.current;let disposed=0,ended=0;
 s.files=['files','sidechat'].map(id=>({id,name:id,body:wb.node('div')}));s.files[1].sideChat={end(){ended++;},dispose(){disposed++;wb.open('other','local');}};wb.split('sidechat');
 await wb.close('sidechat');assert.equal(ended,0);assert.equal(disposed,1);assert.equal(s.splitTab,'files');assert.equal(s.files.length,1);assert.equal(wb.current.id,'other');assert.equal(wb.current.active,'chat');
});

test('model picker preserves current effort only for compatible models',async()=>{
 const ui=await opened();ui.setHandler(async()=>({models:[{id:'model-a',efforts:[{id:'high',name:'Native Strong'},'low']},{id:'model-b',efforts:['low'],defaultEffort:'low'}],currentModel:'model-a',currentEffort:'high'}));await ui.view.models();const dialog=ui.document.querySelector('dialog'),selects=dialog.querySelectorAll('select');assert.equal(selects[1].value,'high');assert.equal(dialog.querySelector('.effort-options').querySelector('input:checked').parentElement.textContent,'Native Strong');assert.equal(dialog.querySelector('.model-options').querySelector('input:checked').value,'model-a');const choice=dialog.querySelector('.model-options').querySelectorAll('input')[1];choice.checked=true;choice.onchange();assert.equal(selects[1].value,'low');assert.equal(dialog.querySelector('.effort-options').querySelector('input:checked').value,'low');
});
test('model picker paints only the matching account catalog before a delayed read',async()=>{
 const ui=await opened(),a='a'.repeat(64),b='b'.repeat(64),catalog=identity=>({models:[{id:identity===a?'model-a':'model-b',efforts:['high']}],cache:{identity,updatedAt:1000},capabilities:{models:true,effort:true}});
 ui.view.state.modelCatalogIdentity=a;ui.setHandler(async()=>catalog(a));await ui.view.models();assert.match(ui.document.querySelector('dialog').textContent,/更新于：/);
 let finish;ui.setHandler(()=>new Promise(resolve=>finish=resolve));const pending=ui.view.models(),dialog=ui.view.modelDialog;assert.match(dialog.querySelector('.model-options').textContent,/model-a/);assert.doesNotMatch(dialog.querySelector('.model-options').textContent,/正在读取模型/);
 ui.view.state.modelCatalogIdentity=b;finish(catalog(a));await pending;assert.equal(dialog.open,false,'old account response closes the obsolete picker');
 let finishB;ui.setHandler(()=>new Promise(resolve=>finishB=resolve));const next=ui.view.models(),newDialog=ui.view.modelDialog;assert.doesNotMatch(newDialog.querySelector('.model-options').textContent,/model-a/);finishB(catalog(b));await next;assert.match(newDialog.querySelector('.model-options').textContent,/model-b/);
});
test('model picker cancel writes nothing and only shows catalog-supported effort',async()=>{
 const ui=await opened();ui.setHandler(async()=>({models:[{id:'model-a',efforts:['high']},{id:'model-b',efforts:[]}],currentModel:'model-a',currentEffort:'high',capabilities:{models:true,effort:true}}));await ui.view.models();const dialog=ui.document.querySelector('dialog');assert.equal(dialog.querySelectorAll('.effort-choice').length,1);assert.equal(dialog.querySelector('.fast-mode-control'),null);const choice=dialog.querySelector('.model-options').querySelectorAll('input')[1];choice.checked=true;choice.onchange();assert.equal(dialog.querySelector('.effort-options').parentElement.hidden,true);dialog.querySelector('.ds-model-actions').querySelector('button').click();assert.equal(dialog.open,false);assert.equal(ui.calls.filter(call=>call.body).length,0);
});
test('DeepSeek model picker writes only the selected model when effort is unsupported',async()=>{
 const ui=fixture();await ui.view.choose('deepseek');await ui.view.open('a');ui.setHandler(async(url,body)=>body?{status:'accepted'}:{models:[{id:'deepseek/model-a',name:'Model A',efforts:[]}],currentModel:'deepseek/model-a',capabilities:{models:true,effort:false}});await ui.view.models();const dialog=ui.document.querySelector('dialog');assert.equal(dialog.querySelector('.effort-options').parentElement.hidden,true);await dialog.querySelector('.ds-model-form').onsubmit({preventDefault(){}});const write=ui.calls.find(call=>call.url.includes('/settings'));assert.equal(write.body.model,'deepseek/model-a');assert.equal('effort' in write.body,false);assert.equal('fastMode' in write.body,false);assert.equal(dialog.open,false);
});
test('model picker cannot save while its catalog refresh is pending',async()=>{
 const ui=await opened(),catalog={models:[{id:'model-a',efforts:['low']},{id:'model-b',efforts:['high']}],currentModel:'model-a',currentEffort:'low',capabilities:{models:true,effort:true}};let finish,reads=0;ui.setHandler(async(url,body)=>{if(body)return {status:'accepted'};if(++reads===2)return new Promise(resolve=>finish=resolve);return catalog;});await ui.view.models();const dialog=ui.document.querySelector('dialog'),form=dialog.querySelector('.ds-model-form'),save=dialog.querySelector('.ds-model-actions').querySelector('.primary');assert.equal(dialog.querySelector('.effort-options').querySelector('input:checked').parentElement.textContent,'低');assert.match(ui.calls.at(-1).url,/section=models/);assert.doesNotMatch(ui.calls.at(-1).url,/refresh=true/);const refreshing=dialog.querySelector('.ds-model-refresh').click();assert.match(ui.calls.at(-1).url,/refresh=true/);assert.equal(save.disabled,true);assert.equal(dialog.querySelector('.model-options').querySelector('input:checked').disabled,true);await form.onsubmit({preventDefault(){}});assert.equal(ui.calls.filter(call=>call.body).length,0);finish(catalog);await refreshing;assert.equal(save.disabled,false);assert.equal(dialog.querySelector('.model-options').querySelector('input:checked').value,'model-a');
});
test('model picker refresh and failed save keep the draft until a successful reload',async()=>{
 const ui=await opened(),catalog={models:[{id:'model-a',efforts:['high']},{id:'model-b',efforts:['low']}],currentModel:'model-a',currentEffort:'high',capabilities:{models:true,effort:true}};let failRead=false;ui.setHandler(async(url,body)=>{if(body)throw Error('write unknown');if(failRead)throw Error('read failed');return catalog;});await ui.view.models();const dialog=ui.document.querySelector('dialog'),choice=dialog.querySelector('.model-options').querySelectorAll('input')[1];choice.checked=true;choice.onchange();failRead=true;await dialog.querySelector('.ds-model-refresh').click();assert.equal(dialog.querySelector('.model-options').querySelector('input:checked').value,'model-b');assert.match(dialog.querySelector('.error').textContent,/read failed/);await dialog.querySelector('.ds-model-form').onsubmit({preventDefault(){}});assert.equal(dialog.open,true);assert.equal(dialog.querySelector('.model-options').querySelector('input:checked').value,'model-b');assert.equal(dialog.querySelector('.ds-model-actions').querySelector('.primary').disabled,true);failRead=false;await dialog.querySelector('.ds-model-refresh').click();assert.equal(dialog.querySelector('.model-options').querySelector('input:checked').value,'model-b');assert.equal(dialog.querySelector('.ds-model-actions').querySelector('.primary').disabled,false);assert.equal(ui.calls.filter(call=>call.body).length,1);
});
test('a late model catalog cannot populate another session or send a settings write',async()=>{
 const ui=await opened();let finish;ui.setHandler(url=>url.includes('/catalog')?new Promise(resolve=>finish=resolve):Promise.resolve({session:{id:'b',title:'Other',status:'idle',capabilities:{}},messages:[]}));const pending=ui.view.models(),dialog=ui.document.querySelector('dialog');await ui.view.open('b');finish({models:[{id:'old',efforts:['high']}],currentModel:'old'});await pending;assert.equal(dialog.open,false);assert.equal(dialog.querySelectorAll('.model-choice').length,0);assert.equal(ui.calls.filter(call=>call.body).length,0);
});
test('Claude creation selects Code or Cowork explicitly and requires the first message',async()=>{
 const ui=fixture();await ui.view.choose('claude');ui.setHandler(async()=>({projects:[{key:'code:/project',name:'Project Code',cwd:'/project',surface:'code'},{key:'cowork:/project',name:'Project Cowork',cwd:'/project',surface:'cowork'}],requiresMessage:true}));await ui.view.create();const dialog=ui.document.querySelector('dialog'),selects=dialog.querySelectorAll('select');assert.equal(selects.length,2);assert.deepEqual(selects[0].children.map(n=>n.value),['code','cowork']);assert.equal(dialog.querySelector('textarea').required,true);selects[0].value='cowork';selects[0].onchange();assert.deepEqual(selects[1].children.map(n=>n.value),['cowork:/project']);
});
test('late send completion preserves text typed while the request was in flight',async()=>{
 const ui=await opened();let finish;ui.setHandler((url,body)=>body?new Promise(resolve=>finish=resolve):Promise.resolve({sessions:[]}));ui.view.input.value='sent';const sending=ui.view.submit();ui.view.input.value='next draft';ui.view.input.oninput();finish({status:'accepted'});await sending;assert.equal(ui.view.input.value,'next draft');assert.equal(ui.view.drafts.get('claude:a'),'next draft');
});
test('detail capabilities gate actual model, permission and queue actions',async()=>{
 const ui=fixture();await ui.view.choose('claude');ui.setHandler(async()=>({session:{id:'a',title:'Chat',status:'active'},capabilities:{models:false,permissions:false,queue:false,steer:false},messages:[]}));await ui.view.open('a');assert.equal(ui.view.modelButton.disabled,true);assert.equal(ui.view.permissionsButton.hidden,true);assert.equal(ui.view.mode.type,'hidden');assert.equal(ui.view.mode.value,'send');
});
test('HTTP clipboard fallback uses the shared selectable copy dialog',async()=>{
 const ui=fixture();await ui.view.choose('deepseek');ui.setHandler(async()=>({session:{id:'a',title:'Chat',status:'idle'},messages:[{id:'1',role:'assistant',text:'copy me'}]}));await ui.view.open('a');await ui.view.messages.querySelector('button').click();assert.equal(ui.document.querySelector('.copy-dialog').querySelector('textarea').value,'copy me');
});
for(const provider of ['claude','deepseek'])test(`${provider} message Copy uses the native clipboard after an actual trusted button click`,async()=>{
 const ui=fixture(),text='第一行\n\n```js\nconst answer = 42;\n```\n🙂';await ui.view.choose(provider);ui.view.renderMessages([{id:'native-copy',role:'assistant',text}]);
 ui.run('window.top=window;globalThis.nativeCopies=[];window.prompt=(command,text)=>{if(command.startsWith("codexbridge-copy:")){nativeCopies.push(text);return "copied";}};');ui.run(fs.readFileSync(path.join(__dirname,'../mobile/shared/web/mobile-clipboard.js'),'utf8'));
 const copy=ui.view.messages.querySelector('button');ui.document.dispatchEvent({type:'click',isTrusted:true,target:copy});await copy.click();assert.deepEqual(Array.from(ui.run('nativeCopies')),[text]);assert.equal(copy.textContent,'已复制');assert.equal(copy.disabled,false);
});
test('native Claude message copy rejection surfaces an error and restores the button',async()=>{
 const ui=await opened(),errors=[];ui.view.renderMessages([{id:'native-failure',role:'assistant',text:'copy fails'}]);ui.document.addEventListener('bridge-message-error',event=>errors.push(event.detail));ui.run('window.top=window;window.prompt=()=>"rejected";');ui.run(fs.readFileSync(path.join(__dirname,'../mobile/shared/web/mobile-clipboard.js'),'utf8'));
 const copy=ui.view.messages.querySelector('button');ui.document.dispatchEvent({type:'click',isTrusted:true,target:copy});await copy.click();assert.deepEqual(errors,['复制失败，请重试']);assert.equal(copy.textContent,'复制');assert.equal(copy.disabled,false);
});
test('permissions render the native catalog choices using shared permissionOption',async()=>{
 const ui=fixture();await ui.view.choose('claude');ui.setHandler(async()=>({session:{id:'a',title:'Chat',status:'idle'},capabilities:{permissions:true},messages:[]}));await ui.view.open('a');ui.setHandler(async(url,body)=>body?{status:'accepted'}:{mode:'default',options:[{value:'default',label:'Default',description:'Ask first'},{value:'acceptEdits',label:'Accept edits',description:'Edit files'}]});await ui.view.permissions();const buttons=ui.document.querySelectorAll('.permission-option');assert.equal(buttons.length,2);assert.equal(buttons[0].getAttribute('aria-pressed'),'true');await buttons[1].click();assert.equal(ui.calls.find(c=>c.body).body.mode,'acceptEdits');
});
test('Claude creation sends the selected surface project and explicit first message only on submit',async()=>{
 const ui=fixture();await ui.view.choose('claude');ui.setHandler(async(url,body)=>body?{status:'ready',result:{id:'created'}}:url.includes('/projects')?{projects:[{key:'cowork:/p',name:'Project',cwd:'/p',surface:'cowork'}],createRequiresMessage:true}:url.includes('/detail')?{session:{id:'created',title:'Custom title',status:'idle'},messages:[]}:{sessions:[]});await ui.view.create();assert.equal(ui.calls.some(c=>c.body),false);const dialog=ui.document.querySelector('dialog');dialog.querySelector('input').value='Custom title';dialog.querySelector('textarea').value='First instruction';await dialog.querySelector('form').onsubmit({preventDefault(){}});const sent=ui.calls.find(c=>c.body);assert.equal(sent.body.projectKey,'cowork:/p');assert.equal(sent.body.firstMessage,'First instruction');assert.equal(sent.body.title,'Custom title');assert.equal(sent.body.sessionId,null);
});
test('Claude CLI creation uses its own project and native first message without a title',async()=>{
 const ui=fixture();await ui.view.choose('claude');ui.setHandler(async(url,body)=>body?{status:'ready',result:{id:'cli-created'}}:url.includes('/projects')?{projects:[{key:'code:/p',name:'Desktop',cwd:'/p',surface:'code'},{key:'cli:opaque',name:'Local CLI',cwd:'/cli',surface:'cli'}],createRequiresMessage:true}:url.includes('/detail')?{session:{id:'cli-created',title:'Native title',status:'idle'},messages:[]}:{sessions:[]});await ui.view.create();const dialog=ui.document.querySelector('dialog'),selects=dialog.querySelectorAll('select');assert.deepEqual(selects[0].children.map(n=>n.value),['code','cli']);selects[0].value='cli';selects[0].onchange();assert.deepEqual(selects[1].children.map(n=>n.value),['cli:opaque']);assert.equal(dialog.querySelector('input').parentElement.hidden,true);dialog.querySelector('textarea').value='Native first request';await dialog.querySelector('form').onsubmit({preventDefault(){}});const sent=ui.calls.find(c=>c.body);assert.equal(sent.body.projectKey,'cli:opaque');assert.equal(sent.body.firstMessage,'Native first request');assert.equal(Object.hasOwn(sent.body,'title'),false);
});

test('shared attachments handle paste, block send until upload, and submit only upload ids',async()=>{
 const ui=fixture();await ui.view.choose('claude');ui.setHandler(async(url,body)=>body?{status:'accepted'}:url.includes('/detail')?{session:{id:'a',title:'Chat',status:'idle'},capabilities:{attachments:true},messages:[]}:{sessions:[]});await ui.view.open('a');let finish;ui.setUploadHandler(()=>new Promise(resolve=>finish=resolve));let prevented=false;ui.view.input.onpaste({clipboardData:{files:[{name:'image.png',type:'image/png',size:16}]},preventDefault(){prevented=true;}});assert.equal(prevented,true);assert.equal(ui.view.send.disabled,true);assert.equal(ui.view.attachments.rows[0].status,'uploading');await ui.view.submit();assert.equal(ui.calls.some(c=>c.body),false);
 finish({ok:true,status:200,json:async()=>({name:'image.png',size:16,image:true})});await new Promise(setImmediate);assert.equal(ui.view.send.disabled,false);const row=ui.view.attachments.rows[0];assert.equal(row.status,'ready');assert.match(ui.calls.find(c=>c.upload).url,/claude\/uploads\?sessionId=a&id=/);assert.equal(ui.calls.find(c=>c.upload).upload.headers['X-CSRF-Token'],'csrf-test');await ui.view.submit();assert.equal(ui.calls.find(c=>c.body).body.attachments[0],row.id);assert.equal(ui.view.attachments.rows.length,0);
});
test('attachment upload remains scoped to its source chat after provider switching',async()=>{
 const ui=fixture();ui.setHandler(async url=>url.includes('/detail')?{session:{id:'a',title:'Chat',status:'idle'},capabilities:{attachments:true},messages:[]}:{sessions:[]});await ui.view.choose('claude');await ui.view.open('a');let finish;ui.setUploadHandler(()=>new Promise(resolve=>finish=resolve));ui.view.attachments.add([{name:'source.png',type:'image/png',size:16}]);await ui.view.choose('deepseek');await ui.view.open('a');finish({ok:true,status:200,json:async()=>({name:'source.png',size:16,image:true})});await new Promise(setImmediate);assert.equal(ui.view.attachments.rows.length,0);assert.equal(ui.view.attachments.drafts.get('["claude","a"]')[0].status,'ready');assert.ok(ui.calls.filter(c=>c.upload).every(c=>c.url.includes('/claude/')));
});
test('unsupported attachment types are explicit and never uploaded',async()=>{
 const ui=await opened();ui.view.state.capabilities={attachments:true};ui.view.updateComposer();ui.view.attachments.add([{name:'notes.txt',type:'text/plain',size:16}]);assert.match(ui.view.attachments.error,/仅支持图片/);assert.equal(ui.calls.some(c=>c.upload),false);assert.equal(ui.view.attachments.rows.length,0);
});

test('a send finishing during polling schedules a fresh detail after the stale response',async()=>{
 const ui=await opened();let finishList,listCalls=0,detailCalls=0;ui.setHandler(async(url,body)=>{if(body)return {status:'accepted'};if(url.includes('/list')){listCalls++;if(listCalls===1)return new Promise(resolve=>finishList=resolve);return {sessions:[]};}return {session:{id:'a',title:'Chat',status:'idle'},messages:[{id:'reply',role:'assistant',text:++detailCalls===1?'old snapshot':'new reply'}]};});
 const polling=ui.view.refresh();ui.view.input.value='send while polling';await ui.view.submit();finishList({sessions:[]});await polling;await new Promise(setImmediate);assert.equal(listCalls,2);assert.match(ui.view.messages.textContent,/new reply/);
});
test('form submission respects the session send capability',async()=>{
 const ui=await opened();ui.view.state.capabilities.send=false;ui.view.updateComposer();ui.view.input.value='must not send';await ui.view.form.onsubmit({preventDefault(){}});assert.equal(ui.calls.some(c=>c.body),false);
});
test('question cards submit exact native question keys and do not invent Harness denial',async()=>{
 const ui=await opened();ui.view.renderRequests([{id:'q1',tool:'AskUserQuestion',needsInput:true,input:{questions:[{id:'native-id',question:'Choose',options:[{label:'A'},{label:'B'}]}]}}]);const form=ui.view.requests.querySelector('form'),inputs=form.querySelectorAll('input');inputs[1].checked=true;inputs[2].value='custom detail';await form.onsubmit({preventDefault(){}});await Promise.resolve();const body=ui.calls.find(c=>c.body).body;assert.equal(body.requestId,'q1');assert.equal(body.decision,'accept');assert.deepEqual(Array.from(body.answers['native-id'].answers),['B','custom detail']);
 await ui.view.choose('deepseek');await ui.view.open('a');ui.view.renderRequests([{id:'q2',tool:'Question',needsInput:true,input:{questions:[]}}]);assert.equal(ui.view.requests.querySelectorAll('button').length,1);ui.view.renderRequests([{id:'approval',tool:'Write',needsInput:false,input:{reason:'write a file'}}]);await ui.view.requests.querySelectorAll('button')[1].click();assert.equal(ui.calls.filter(c=>c.body).at(-1).body.decision,'decline');
});
test('skills send only selectable installed skills and preserve failed-send selection',async()=>{
 const ui=fixture();ui.setHandler(async(url,body)=>body?{status:'unknown'}:url.includes('/catalog')?{skills:[{id:'review',name:'Review',kind:'skill',selectable:true},{id:'installed:tools',name:'Tools',kind:'plugin',selectable:false}]}:url.includes('/detail')?{session:{id:'a',title:'Chat',status:'idle'},capabilities:{skills:true},messages:[]}:{sessions:[]});await ui.view.choose('deepseek');await ui.view.open('a');assert.equal(ui.view.skillsButton.hidden,false);await ui.view.skills();const options=ui.document.querySelectorAll('.skill-option'),checks=options.map(n=>n.querySelector('input'));assert.equal(checks[1].disabled,true);checks[0].checked=true;checks[0].onchange();ui.view.input.value='review this';await ui.view.submit();assert.deepEqual(Array.from(ui.calls.find(c=>c.body).body.plugins),['review']);assert.match(ui.view.skillPills.textContent,/Review/);
 await ui.view.choose('claude');await ui.view.open('a');assert.equal(ui.view.skillPills.textContent,'');await ui.view.choose('deepseek');await ui.view.open('a');assert.match(ui.view.skillPills.textContent,/Review/);
});
test('Claude request-size limit stays in attachment help and rejects oversized images before upload',async()=>{
 const ui=fixture();await ui.view.choose('claude');ui.setHandler(async()=>({session:{id:'a',title:'Chat',status:'idle'},capabilities:{attachments:true},maxRequestBytes:10*1024*1024,messages:[]}));await ui.view.open('a');assert.ok(!ui.view.composerBody.querySelector('.ds-attachment-hint'));assert.match(ui.view.attachButton.title,/10 MiB/);ui.view.attachments.add([{name:'large.png',type:'image/png',size:8*1024*1024}]);assert.equal(ui.calls.some(c=>c.upload),false);assert.match(ui.view.attachments.error,/10 MiB/);
});
test('history updates retain expanded activities and honor visibility settings',async()=>{
 const ui=await opened();ui.view.renderMessages([{id:'step',role:'activity',title:'Write',text:'before'}]);ui.view.messageList.children[0].open=true;ui.view.renderMessages([{id:'step',role:'activity',title:'Write',text:'after'},{id:'thought',role:'reasoning',text:'summary'}]);assert.equal(ui.view.messageList.children[0].open,true);ui.document.documentElement.dataset.showProcess='false';ui.document.documentElement.dataset.showReasoning='false';ui.view.renderMessages(ui.view.lastMessages);assert.ok(ui.view.messageList.children.every(n=>n.hidden));
});

test('skill selection clears after acceptance and expired choices cannot be submitted',async()=>{
 const ui=fixture();let available=true;ui.setHandler(async(url,body)=>body?{status:'accepted'}:url.includes('/catalog')?{skills:available?[{id:'review',name:'Review',selectable:true}]:[]}:url.includes('/detail')?{session:{id:'a',title:'Chat',status:'idle'},capabilities:{skills:true},messages:[]}:{sessions:[]});await ui.view.choose('claude');await ui.view.open('a');await ui.view.skills();let dialog=ui.document.querySelector('dialog'),check=dialog.querySelector('input[type=checkbox]')||dialog.querySelector('.skill-option').querySelector('input');check.checked=true;check.onchange();dialog.close();ui.view.input.value='review';await ui.view.submit();assert.equal(ui.view.skillPills.textContent,'');
 await ui.view.skills();dialog=ui.document.querySelector('dialog');check=dialog.querySelector('.skill-option').querySelector('input');check.checked=true;check.onchange();dialog.close();available=false;await ui.view.skills();assert.ok(ui.view.skillPills.children[0].classList.contains('invalid'));const count=ui.calls.filter(c=>c.body).length;ui.view.input.value='do not submit expired skill';await ui.view.submit();assert.equal(ui.calls.filter(c=>c.body).length,count);assert.match(ui.view.error.textContent,/失效/);
});
test('request updates and language changes preserve answers already entered',async()=>{
 const ui=await opened(),question={id:'q1',tool:'AskUserQuestion',needsInput:true,input:{questions:[{id:'native-id',question:'Choose',options:[{label:'A'},{label:'B'}]}]}};ui.view.renderRequests([question]);let inputs=ui.view.requests.querySelector('form').querySelectorAll('input');inputs[1].checked=true;inputs[2].value='written answer';ui.view.renderRequests([question,{id:'approval',tool:'Write',input:{reason:'write'}}]);inputs=ui.view.requests.querySelector('form').querySelectorAll('input');assert.equal(inputs[1].checked,true);assert.equal(inputs[2].value,'written answer');ui.view.state.requests=[question];ui.view.relabel();inputs=ui.view.requests.querySelector('form').querySelectorAll('input');assert.equal(inputs[1].checked,true);assert.equal(inputs[2].value,'written answer');
});


test('provider activity dots use explicit statuses and never turn ordinary idle into completed',async()=>{
 const ui=fixture();let rows=[{id:'same',title:'Run',status:'active',backend:'code'},{id:'idle',title:'Idle',status:'idle',backend:'code'},{id:'ask',title:'Ask',status:'active',requests:[{id:'permission'}],backend:'cowork'}];ui.setHandler(async()=>({sessions:rows}));await ui.view.choose('claude');const byId=id=>ui.view.rowsRoot.querySelectorAll('.session').find(n=>n.dataset.id===id);assert.ok(byId('same').querySelector('.session-indicator').classList.contains('running'));assert.equal(byId('idle').querySelector('.session-indicator'),null);assert.ok(byId('ask').querySelector('.session-indicator').classList.contains('awaiting'));
 rows=[{id:'same',title:'Run',status:'idle',turnId:'native-turn',turnStatus:'completed',backend:'code'}];await ui.view.refresh();assert.ok(byId('same').querySelector('.session-indicator').classList.contains('completed'));await ui.view.choose('deepseek');assert.equal(byId('same').querySelector('.session-indicator'),null);await ui.view.choose('claude');assert.ok(byId('same').querySelector('.session-indicator').classList.contains('completed'));
 ui.setHandler(async()=>({session:{id:'same',title:'Run',status:'idle',turnId:'native-turn',turnStatus:'completed'},messages:[]}));await ui.view.open('same');assert.equal(byId('same').querySelector('.session-indicator'),null);
});
test('Claude project groups remember their collapsed state across refresh and provider switches',async()=>{
 const ui=fixture();ui.setHandler(async()=>({sessions:[{id:'a',title:'Code chat',backend:'code',cwd:'/code'},{id:'b',title:'Cowork chat',backend:'cowork',cwd:'/cowork'}]}));await ui.view.choose('claude');ui.view.listMode.value='project';ui.view.renderList();let group=ui.view.rowsRoot.querySelectorAll('details').find(n=>n.querySelector('summary')?.textContent==='/code');assert.ok(group);group.open=false;group.ontoggle();ui.view.listStamp='';ui.view.renderList();group=ui.view.rowsRoot.querySelectorAll('details').find(n=>n.querySelector('summary')?.textContent==='/code');assert.equal(group.open,false);await ui.view.choose('deepseek');await ui.view.choose('claude');group=ui.view.rowsRoot.querySelectorAll('details').find(n=>n.querySelector('summary')?.textContent==='/code');assert.equal(group.open,false);assert.equal(ui.view.rowsRoot.querySelectorAll('details').find(n=>n.querySelector('summary')?.textContent==='/cowork').open,true);
});
test('composer has no redundant file button while Workbench retains files and split tabs',async()=>{
 const ui=await opened();assert.equal(ui.view.tools.querySelectorAll('button').some(n=>n.textContent==='文件'),false);ui.view.workbench.files();await Promise.resolve();assert.ok(ui.view.workbench.current.files.some(n=>n.id==='files'));ui.view.workbench.split('files');assert.equal(ui.view.workbench.current.splitTab,'files');
});
test('opening a chat and polling share one detail read and show its first successful response',async()=>{
 for(const provider of ['claude','deepseek']){
  const ui=fixture();ui.setHandler(async()=>({sessions:[{id:'a',title:'First response',backend:'code'}]}));await ui.view.choose(provider);
  const pending=[];ui.setHandler(url=>url.includes('/detail')?new Promise(resolve=>pending.push(resolve)):Promise.resolve({sessions:ui.view.rows}));
  const opening=ui.view.open('a'),polling=ui.view.refresh();assert.equal(pending.length,1,'polling must join the opening read');
  pending[0]({session:{id:'a',title:'First response',status:'idle'},capabilities:{send:true},messages:[{id:'first',role:'assistant',text:'first successful history'}]});await Promise.all([opening,polling]);
  assert.match(ui.view.messages.textContent,/first successful history/);assert.equal(ui.view.send.disabled,false);
 }
});

test('selecting the current provider keeps the live conversation without starting new reads',async()=>{
 const ui=await opened();ui.view.input.value='keep typing';const count=ui.calls.length,message=ui.view.messages.textContent;ui.setHandler(()=>new Promise(()=>{}));
 const selected=ui.view.choose('claude');assert.equal(ui.calls.length,count);assert.equal(ui.view.messages.textContent,message);assert.equal(ui.view.input.value,'keep typing');assert.equal(ui.view.send.disabled,false);await selected;
});

test('a pending history read does not block subsequent session list refreshes',async()=>{
 const ui=await opened();let finish,listReads=0,detailReads=0;ui.setHandler(url=>url.includes('/detail')?(detailReads++,new Promise(resolve=>finish=resolve)):Promise.resolve({sessions:[{id:'a',title:'List '+(++listReads),backend:'code'}]}));
 const first=ui.view.refresh();await new Promise(setImmediate);const second=ui.view.refresh();await new Promise(setImmediate);
 assert.equal(listReads,2);assert.match(ui.view.rowsRoot.textContent,/List 2/);assert.equal(detailReads,1,'list polling must keep joining the pending detail');
 finish({session:{id:'a',title:'Finished',status:'idle'},messages:[]});await Promise.all([first,second]);
});

test('a completed mutation cannot reuse an earlier history read as fresh state',async()=>{
 const ui=await opened(),pending=[];ui.setHandler((url,body)=>body?Promise.resolve({status:'accepted'}):url.includes('/detail')?new Promise(resolve=>pending.push(resolve)):Promise.resolve({sessions:[]}));
 const old=ui.view.detail(ui.view.generation);await ui.view.mutate('settings',{model:'new-model'});assert.equal(pending.length,2,'write completion must start a new detail read');
 pending[0]({session:{id:'a',title:'Obsolete',model:'obsolete-model',status:'idle'},messages:[{id:'old',role:'assistant',text:'obsolete history'}]});await old;
 assert.doesNotMatch(ui.view.messages.textContent,/obsolete history/);assert.equal(ui.view.send.disabled,true);
 pending[1]({session:{id:'a',title:'Current',model:'new-model',status:'idle'},messages:[{id:'new',role:'assistant',text:'current history'}]});await new Promise(setImmediate);assert.match(ui.view.messages.textContent,/current history/);assert.equal(ui.view.send.disabled,false);
});

test('cached, failed and disconnected details remain readable without allowing task mutations',async()=>{
 for(const mode of ['cache','failure','disconnected','inventory']){
  const ui=fixture(),value={session:{id:'a',title:'Active chat',status:'active',requests:[{id:'approval',tool:'Write',input:{}}]},capabilities:{send:true,stop:true},messages:[{id:'m',role:'assistant',text:'readable history'}]};
  ui.setHandler(async url=>url.includes('/detail')?value:{sessions:[{id:'a',title:'Active chat',backend:'code'}]});await ui.view.choose('claude');await ui.view.open('a');assert.equal(ui.view.stop.disabled,false);
  let opening,finish;if(mode==='cache'){ui.view.backToList();ui.setHandler(()=>new Promise(resolve=>finish=resolve));opening=ui.view.open('a');}
  else if(mode==='inventory')ui.view.setClientStates([{id:'claude',enabled:true,connected:false,connectionState:'idle'}]);
  else{ui.setHandler(async url=>{if(url.includes('/detail')){if(mode==='failure')throw Error('offline');return {...value,connected:false};}return {sessions:ui.view.rows};});await ui.view.refresh();await new Promise(setImmediate);}
  assert.match(ui.view.messages.textContent,/readable history/);assert.equal(ui.view.send.disabled,true,mode);assert.equal(ui.view.stop.disabled,true,mode);const writes=ui.calls.filter(c=>c.body).length;
  await ui.view.stop.click();await ui.view.mutate('respond',{requestId:'approval',decision:'accept'});assert.equal(ui.calls.filter(c=>c.body).length,writes,mode);
  if(finish){finish(value);await opening;}
 }
});

test('visible connected providers prefetch only their lists and reuse the cache on first selection',async()=>{
 const ui=fixture(),clients=[{id:'codex',enabled:true,connected:true},{id:'claude',enabled:true,connected:true},{id:'deepseek',enabled:false,connected:true}];ui.setHandler(async()=>({sessions:[{id:'a',title:'Prefetched list',backend:'code'}]}));
 ui.document.hidden=true;ui.view.setClientStates(clients);assert.equal(ui.calls.length,0);ui.document.hidden=false;ui.document.dispatchEvent({type:'visibilitychange'});await new Promise(setImmediate);
 assert.deepEqual(ui.calls.map(c=>c.url),['/api/desktop-sessions/claude/list']);ui.view.setClientStates(clients);ui.document.dispatchEvent({type:'visibilitychange'});await new Promise(setImmediate);assert.equal(ui.calls.length,1);
 let finish;ui.setHandler(()=>new Promise(resolve=>finish=resolve));const selecting=ui.view.choose('claude');assert.match(ui.view.rowsRoot.textContent,/Prefetched list/);assert.equal(ui.calls.some(c=>c.url.includes('/detail')),false);finish({sessions:[]});await selecting;
});

test('history caching evicts older chats and does not retain an oversized transcript',async()=>{
 const ui=fixture();ui.setHandler(async url=>url.includes('/detail')?{session:{id:new URL(url,'http://fixture').searchParams.get('sessionId'),title:'Chat',status:'idle'},messages:[{id:'m',role:'assistant',text:'cached '+new URL(url,'http://fixture').searchParams.get('sessionId')}]}:{sessions:[]});await ui.view.choose('claude');
 for(let i=0;i<13;i++)await ui.view.open(String(i));ui.view.backToList();let finish;ui.setHandler(()=>new Promise(resolve=>finish=resolve));const old=ui.view.open('0');assert.equal(ui.view.messages.textContent.includes('cached 0'),false);finish({session:{id:'0',title:'Chat',status:'idle'},messages:[]});await old;
 ui.view.backToList();const recent=ui.view.open('12');assert.match(ui.view.messages.textContent,/cached 12/);finish({session:{id:'12',title:'Chat',status:'idle'},messages:[]});await recent;
 ui.setHandler(async()=>({session:{id:'huge',title:'Large',status:'idle'},messages:[{id:'m',role:'assistant',text:'oversized marker '+('x'.repeat(4*1024*1024))}]}));await ui.view.open('huge');ui.view.backToList();ui.setHandler(()=>new Promise(resolve=>finish=resolve));const huge=ui.view.open('huge');assert.equal(ui.view.messages.textContent.includes('oversized marker'),false);finish({session:{id:'huge',title:'Large',status:'idle'},messages:[]});await huge;
});

test('an invalidated list failure cannot replace the status of a newer successful read',async()=>{
 const ui=await opened();let rejectOld,reads=0;ui.setHandler((url,body)=>body?Promise.resolve({status:'accepted'}):url.includes('/list')?++reads===1?new Promise((_,reject)=>rejectOld=reject):Promise.resolve({sessions:[{id:'a',title:'Fresh list',backend:'code'}]}):Promise.resolve({session:{id:'a',title:'Fresh',status:'idle'},messages:[]}));
 const stale=ui.view.refresh();await new Promise(setImmediate);await ui.view.mutate('settings',{model:'new'});await new Promise(setImmediate);rejectOld(Error('obsolete list failure'));await stale;
 assert.match(ui.view.rowsRoot.textContent,/Fresh list/);assert.equal(ui.view.listStatus.textContent,'');
});

test('account changes, disabling and logout reject old background list and history replies',async()=>{
 for(const mode of ['account','disable','logout']){
  const ui=fixture(),client={id:'claude',enabled:true,connected:true},pending=[];ui.setHandler(url=>new Promise(resolve=>pending.push({url,resolve})));ui.view.setClientStates([client]);const listing=ui.view.choose('claude'),opening=ui.view.open('a');await ui.view.choose('codex');
  if(mode==='account')await ui.view.accountChanged('claude',{activeId:'old'},{activeId:'new'});else if(mode==='disable')ui.view.setClientStates([{...client,enabled:false}]);else ui.view.clear();
  for(const call of pending)call.resolve(call.url.includes('/detail')?{session:{id:'a',title:'Old identity',status:'idle'},messages:[{id:'secret',role:'assistant',text:'old identity history'}]}:{sessions:[{id:'a',title:'Old identity list',backend:'code'}]});await Promise.all([listing,opening]);
  const current=[];ui.setHandler(url=>new Promise(resolve=>current.push({url,resolve})));ui.view.setClientStates([client]);const selecting=ui.view.choose('claude');assert.equal(ui.view.rowsRoot.textContent.includes('Old identity'),false,mode);assert.equal(ui.view.messages.textContent.includes('old identity history'),false,mode);
  for(const call of current)call.resolve(call.url.includes('/detail')?{session:{id:'a',title:'Current identity',status:'idle'},messages:[]}:{sessions:[]});await selecting;
 }
});

test('reads started before and during a write never replace its completion refresh',async()=>{
 const ui=await opened(),pending=[];let accept;ui.setHandler((url,body)=>body?new Promise(resolve=>accept=resolve):url.includes('/detail')?new Promise(resolve=>pending.push(resolve)):Promise.resolve({sessions:[]}));
 const old=ui.view.detail(ui.view.generation),writing=ui.view.mutate('settings',{model:'new'}),during=ui.view.refresh();pending[0]({session:{id:'a',title:'Before',status:'idle'},messages:[{id:'old',role:'assistant',text:'before write'}]});await old;assert.equal(ui.view.messages.textContent.includes('before write'),false);
 accept({status:'accepted'});await writing;assert.equal(pending.length,3);pending[1]({session:{id:'a',title:'During',status:'idle'},messages:[{id:'mid',role:'assistant',text:'during write'}]});await during;await new Promise(setImmediate);assert.equal(ui.view.messages.textContent.includes('during write'),false);assert.equal(ui.view.send.disabled,true);
 pending[2]({session:{id:'a',title:'After',status:'idle'},messages:[{id:'new',role:'assistant',text:'after write'}]});await new Promise(setImmediate);assert.match(ui.view.messages.textContent,/after write/);assert.equal(ui.view.send.disabled,false);
});

test('an old account write cannot invalidate the new account list refresh',async()=>{
 const ui=await opened();let accept,finishList;ui.setHandler((url,body)=>body?new Promise(resolve=>accept=resolve):Promise.resolve({sessions:[]}));const writing=ui.view.mutate('settings',{model:'new'});
 ui.setHandler(()=>new Promise(resolve=>finishList=resolve));const changing=ui.view.accountChanged('claude',{activeId:'old'},{activeId:'new'});accept({status:'accepted'});await writing;
 finishList({sessions:[{id:'b',title:'New account list',backend:'code'}]});await changing;assert.match(ui.view.rowsRoot.textContent,/New account list/);assert.equal(ui.view.listStatus.textContent,'');
});

test('provider return paints cached list and selected history before either network response',async()=>{
 const ui=fixture();ui.setHandler(async url=>url.includes('/detail')?{session:{id:'a',title:'Cached chat',status:'idle'},capabilities:{attachments:true,skills:true},messages:[{id:'m',role:'assistant',text:'cached answer'}]}:{sessions:[{id:'a',title:'Cached chat',backend:'code'}]});await ui.view.choose('claude');await ui.view.open('a');ui.view.input.value='unfinished';ui.view.input.oninput();ui.view.selectedSkills().set('review',{id:'review',name:'Review'});ui.view.attachments.add([{name:'draft.png',type:'image/png',size:16}]);await new Promise(setImmediate);await ui.view.choose('deepseek');
 const pending=[];ui.setHandler(url=>new Promise(resolve=>pending.push({url,resolve})));const returning=ui.view.choose('claude');assert.match(ui.view.rowsRoot.textContent,/Cached chat/);assert.equal(ui.view.sid,'a');assert.match(ui.view.messages.textContent,/cached answer/);assert.equal(ui.view.input.value,'unfinished');assert.match(ui.view.skillPills.textContent,/Review/);assert.equal(ui.view.attachments.rows.length,1);assert.equal(ui.view.attachments.rows[0].status,'ready');assert.equal(ui.view.send.disabled,true);
 for(const call of pending)call.resolve(call.url.includes('/detail')?{session:{id:'a',title:'Fresh chat',status:'idle'},capabilities:{attachments:true,skills:true},messages:[{id:'m',role:'assistant',text:'fresh answer'}]}:{sessions:[{id:'a',title:'Fresh chat',backend:'code'}]});await returning;assert.match(ui.view.messages.textContent,/fresh answer/);assert.equal(ui.view.input.value,'unfinished');assert.equal(ui.view.send.disabled,false);
});

test('reads finishing after a provider switch warm only their own list and history cache',async()=>{
 const ui=fixture(),pending=[];ui.setHandler(url=>new Promise(resolve=>pending.push({url,resolve})));const first=ui.view.choose('claude');await ui.view.choose('codex');pending[0].resolve({sessions:[{id:'a',title:'Background list',backend:'code'}]});await first;
 const returning=ui.view.choose('claude');assert.match(ui.view.rowsRoot.textContent,/Background list/);pending[1].resolve({sessions:[{id:'a',title:'Background list',backend:'code'}]});await returning;
 const opening=ui.view.open('a'),history=pending[2];ui.setHandler(async()=>({sessions:[{id:'b',title:'DSH list'}]}));await ui.view.choose('deepseek');history.resolve({session:{id:'a',title:'Background list',status:'idle'},capabilities:{send:true},messages:[{id:'m',role:'assistant',text:'Background history'}]});await opening;
 assert.match(ui.view.rowsRoot.textContent,/DSH list/);assert.doesNotMatch(ui.view.messages.textContent,/Background history/);
 const fresh=[];ui.setHandler(url=>new Promise(resolve=>fresh.push({url,resolve})));const back=ui.view.choose('claude');assert.match(ui.view.messages.textContent,/Background history/);assert.equal(ui.view.send.disabled,true);
 for(const call of fresh)call.resolve(call.url.includes('/detail')?{session:{id:'a',title:'Fresh',status:'idle'},messages:[]}:{sessions:[]});await back;
});
test('cached history survives refresh failure and late errors cannot cross providers sharing an id',async()=>{
 const ui=await opened();ui.view.renderMessages([{id:'saved',role:'assistant',text:'saved'}]);let fail;ui.setHandler(()=>new Promise((_,reject)=>fail=reject));const old=ui.view.open('a');const rejectOld=fail;ui.setHandler(async url=>url.includes('/detail')?{session:{id:'a',title:'DSH chat',status:'idle'},messages:[{id:'new',role:'assistant',text:'DSH answer'}]}:{sessions:[]});await ui.view.choose('deepseek');await ui.view.open('a');rejectOld(Error('Claude late failure'));await old;assert.doesNotMatch(ui.view.error.textContent,/Claude late failure/);assert.match(ui.view.messages.textContent,/DSH answer/);
});


test('reopening cached history keeps its scroll and draft when the background connection fails',async()=>{
 const ui=fixture();ui.setHandler(async url=>url.includes('/detail')?{session:{id:'a',title:'Saved title',status:'idle'},messages:[{id:'saved',role:'assistant',text:'saved history'}]}:{sessions:[{id:'a',title:'Saved title',backend:'code'}]});await ui.view.choose('claude');await ui.view.open('a');ui.view.input.value='saved draft';ui.view.messages.scrollHeight=1000;ui.view.messages.scrollTop=42;ui.view.backToList();ui.setHandler(async()=>{throw Error('offline');});const reopening=ui.view.open('a');assert.match(ui.view.messages.textContent,/saved history/);assert.equal(ui.view.messages.scrollTop,42);assert.equal(ui.view.input.value,'saved draft');await reopening;assert.match(ui.view.messages.textContent,/saved history/);assert.equal(ui.view.send.disabled,true);assert.match(ui.view.notice.textContent,/offline/);
});
test('logout clears provider history and ignores detail responses arriving after clear',async()=>{
 const ui=fixture();ui.setHandler(async url=>url.includes('/detail')?{session:{id:'a',title:'Private title',status:'idle'},messages:[{id:'saved',role:'assistant',text:'private history'}]}:{sessions:[{id:'a',title:'Private title',backend:'code'}]});await ui.view.choose('claude');await ui.view.open('a');let complete;ui.setHandler(()=>new Promise(resolve=>complete=resolve));const late=ui.view.detail(ui.view.generation);ui.view.clear();complete({session:{id:'a',title:'Late private title',status:'idle'},messages:[{id:'late',role:'assistant',text:'late private history'}]});await late;assert.equal(ui.view.providerViews.size,0);assert.equal(ui.view.detailCache.size,0);assert.equal(ui.view.messages.textContent,'');assert.equal(ui.view.rowsRoot.textContent,'');ui.setHandler(async()=>({sessions:[]}));await ui.view.choose('claude');assert.equal(ui.view.rows.length,0);assert.equal(ui.view.sid,null);
});
test('provider switching retains Workbench file tabs without mixing their session scope',async()=>{
 const ui=fixture();ui.setHandler(async url=>url.includes('/workspace')?{entries:[],total:0,project:'/project'}:url.includes('/detail')?{session:{id:'a',title:'Chat',status:'idle'},messages:[]}:{sessions:[]});await ui.view.choose('claude');await ui.view.open('a');ui.view.workbench.files();await Promise.resolve();const claude=ui.view.workbench.current;await ui.view.choose('deepseek');await ui.view.open('a');assert.notEqual(ui.view.workbench.current.key,claude.key);assert.equal(ui.view.workbench.current.files.length,0);await ui.view.choose('claude');assert.equal(ui.view.workbench.current,claude);assert.ok(ui.view.workbench.current.files.some(tab=>tab.id==='files'));
});


test('cached permission cards stay hidden until refreshed even when language changes',async()=>{
 const ui=fixture();ui.setHandler(async url=>url.includes('/detail')?{session:{id:'a',title:'Chat',status:'idle',requests:[{id:'old',tool:'Write',input:{}}]},messages:[]}:{sessions:[]});await ui.view.choose('claude');await ui.view.open('a');ui.view.backToList();let finish;ui.setHandler(()=>new Promise(resolve=>finish=resolve));const opening=ui.view.open('a');ui.view.relabel();assert.equal(ui.view.requests.children.length,0);finish({session:{id:'a',title:'Chat',status:'idle',requests:[]},messages:[]});await opening;assert.equal(ui.view.requests.children.length,0);
});

test('desktop chat notification policies read and save within their provider and session',async()=>{
 const ui=await opened();ui.setHandler(async(url,body)=>({available:true,requests:'inherit',completion:'off'}));await ui.view.notifications();const dialog=ui.document.querySelector('dialog'),form=dialog.querySelector('form'),selects=form.querySelectorAll('select');assert.equal(selects[0].value,'inherit');assert.equal(selects[1].value,'off');selects[0].value='on';selects[1].value='inherit';await form.onsubmit({preventDefault(){}});const write=ui.calls.filter(c=>c.body).at(-1);assert.match(write.url,/desktop-sessions\/claude\/notifications\?sessionId=a/);assert.deepEqual(JSON.parse(JSON.stringify(write.body)),{requests:'on',completion:'inherit'});assert.equal(dialog.open,false);assert.match(ui.notices.at(-1),/聊天提醒设置已保存/);
});
test('notification policy dialogs cannot save into a different selected client',async()=>{
 const ui=await opened();ui.setHandler(async()=>({available:false,requests:'inherit',completion:'inherit'}));await ui.view.notifications();const dialog=ui.document.querySelector('dialog'),form=dialog.querySelector('form');await ui.view.choose('deepseek');const before=ui.calls.filter(c=>c.body).length;await form.onsubmit({preventDefault(){}});assert.equal(ui.calls.filter(c=>c.body).length,before);assert.equal(dialog.open,false);
});
test('late notification-policy reads do not populate a different chat',async()=>{
 const ui=await opened();let finish;ui.setHandler(()=>new Promise(resolve=>finish=resolve));const read=ui.view.notifications(),dialog=ui.document.querySelector('dialog');ui.view.backToList();finish({requests:'on'});await read;assert.equal(dialog.open,false);assert.equal(dialog.querySelector('form'),null);
});

test('account switch isolates drafts and clears only that client history, permissions, uploads and workbench',async()=>{
 const ui=await opened(),view=ui.view;view.input.value='old account draft';view.saveDraft();view.drafts.set('deepseek:same','other client draft');view.detailCache.set('["deepseek","same"]',{messages:[{text:'keep'}]});view.providerViews.set('deepseek',{sid:'same'});view.workbench.sessions.set('deepseek|local|same',{provider:'deepseek',files:[]});view.attachments.drafts.set('["deepseek","same"]',[{id:'keep-upload'}]);
 view.attachments.open('["claude","a"]');view.attachments.rows.push({id:'old-upload',status:'ready'});view.attachments.save(view.attachments.key,view.attachments.rows);view.selectedSkills().set('old-plugin',{name:'old'});
 await view.accountChanged('claude',{activeId:'first'},{activeId:'second'});assert.equal(view.sid,null);assert.equal(view.input.value,'');assert.equal(view.drafts.has('claude:a'),false);assert.equal(view.detailCache.has('["claude","a"]'),false);assert.equal(view.skillSelections.has('["claude","a"]'),false);assert.equal(view.attachments.drafts.has('["claude","a"]'),false);assert.equal(view.workbench.sessions.has('claude|local|a'),false);
 assert.equal(view.drafts.get('deepseek:same'),'other client draft');assert.equal(view.detailCache.has('["deepseek","same"]'),true);assert.equal(view.workbench.sessions.has('deepseek|local|same'),true);assert.equal(view.attachments.drafts.has('["deepseek","same"]'),true);assert.equal(ui.run('sessionStorage.getItem(\'attachments:["claude","a"]\')'),undefined);
 assert.equal(view.accountDrafts.get('["claude",["first"]]').get('claude:a'),'old account draft');await view.accountChanged('claude',{activeId:'second'},{activeId:'first'});assert.equal(view.drafts.get('claude:a'),'old account draft');
});

test('permission label and context follow the selected session, including external changes',async()=>{
 const ui=await opened();ui.view.applyDetail({session:{id:'a',title:'Chat',permissionMode:'acceptEdits',contextUsage:{usedTokens:40000,contextWindow:200000}},capabilities:{permissions:true},messages:[]});
 assert.equal(ui.view.permissionsButton.textContent,'允许文件编辑');assert.match(ui.view.contextButton.title,/20%/);
 ui.view.applyDetail({session:{id:'a',title:'Chat',permissionMode:'plan',contextUsage:null},capabilities:{permissions:true},messages:[]});
 assert.equal(ui.view.permissionsButton.textContent,'计划模式');assert.doesNotMatch(ui.view.contextButton.title,/20%/);
});


test('accepted permission change updates label before polling and ignores an old session',async()=>{
 const ui=await opened();ui.view.refresh=()=>{};
 ui.setHandler(async()=>({status:'accepted',mode:'plan'}));
 await ui.view.mutate('access',{mode:'plan'});
 assert.equal(ui.view.permissionsButton.textContent,'计划模式');
 let finish;ui.view.freshDetail=true;ui.setHandler(()=>new Promise(resolve=>finish=resolve));
 const changing=ui.view.mutate('access',{mode:'acceptEdits'});
 ui.view.sid='b';ui.view.state={id:'b',permissionMode:'default'};
 finish({status:'accepted',mode:'acceptEdits'});await changing;
 assert.equal(ui.view.state.permissionMode,'default');
});

test('context monitor remains discoverable and distinguishes unsupported from missing readings',async()=>{
 for(const language of ['zh','en']){
  const ui=await opened();ui.run(`BridgeI18n.setLanguage('${language}')`);
  const apply=(usage,status)=>ui.view.applyDetail({session:{id:'a',title:'Chat',contextUsage:usage,contextUsageStatus:status},messages:[]});
  apply({usedTokens:40000,contextWindow:200000},'available');
  assert.equal(ui.view.contextButton.textContent,'');assert.equal(ui.view.contextButton.children.length,1);assert.equal(ui.view.contextButton.hidden,false);
  assert.equal(ui.view.contextRing.className,'context-usage-ring');assert.match(ui.view.contextButton.title,/20%/);
  assert.equal(ui.view.contextRing.getAttribute('aria-hidden'),'true');
  ui.view.contextDetails();let dialog=ui.document.querySelector('dialog');assert.match(dialog.textContent,/40,000/);assert.match(dialog.textContent,/20\.0%/);dialog.close();
  apply({usedTokens:600,contextWindow:null,measurement:'lastPrompt',model:'fixture-model',compacted:true},'available');
  assert.equal(ui.view.contextButton.textContent,'');assert.ok(ui.view.contextRing.classList.contains('unknown'));assert.doesNotMatch(ui.view.contextButton.title,/0%/);
  ui.view.contextDetails();dialog=ui.document.querySelector('dialog');assert.match(dialog.textContent,/600/);assert.match(dialog.textContent,/fixture-model/);assert.doesNotMatch(dialog.textContent,/3\.0%/);dialog.close();
  apply(null,'unsupported');assert.equal(ui.view.contextButton.textContent,'');assert.doesNotMatch(ui.view.contextButton.title,/0%|20%/);
  ui.view.contextDetails();dialog=ui.document.querySelector('dialog');assert.match(dialog.textContent,language==='en'?/does not expose context usage/:/未提供上下文用量接口/);dialog.close();
  apply(null,'unavailable');ui.view.contextDetails();dialog=ui.document.querySelector('dialog');assert.match(dialog.textContent,language==='en'?/update automatically when available/:/有可用数据时会自动更新/);assert.doesNotMatch(dialog.textContent,language==='en'?/does not expose/:/未提供上下文用量接口/);
 }
});

for(const provider of ['claude','deepseek'])test(`${provider} manual refresh joins polling and foreground reads and waits for the list result`,async()=>{
 const ui=fixture();await ui.view.choose(provider);let finish;ui.setHandler(()=>new Promise(resolve=>finish=resolve));const before=ui.calls.length;
 const polling=ui.view.refresh(),manual=ui.view.listSync.update(true);ui.document.dispatchEvent({type:'visibilitychange'});assert.equal(ui.calls.length-before,1);assert.match(ui.view.listHint.textContent,/正在刷新/);assert.equal(ui.view.listSync.busy,true);
 finish({sessions:[{id:'new',title:'New result',backend:'code'}]});await Promise.all([polling,manual]);await new Promise(setImmediate);assert.equal(ui.calls.length-before,1,'no redundant trailing list read');assert.match(ui.view.rowsRoot.textContent,/New result/);assert.match(ui.view.listHint.textContent,/已更新/);assert.equal(ui.calls.some(c=>c.body),false);
});
test('manual refresh failure and disconnected responses remain retryable without fetching a selected chat',async()=>{
 const ui=await opened(),before=ui.calls.length;
 ui.setHandler(async()=>{throw Error('offline');});await ui.view.listSync.update(true);assert.match(ui.view.listHint.textContent,/点击重试/);assert.equal(ui.view.sid,'a');
 ui.setHandler(async()=>({connected:false,sessions:[]}));await ui.view.listHint.click();assert.match(ui.view.listHint.textContent,/点击重试/);
 ui.setHandler(async()=>({sessions:[{id:'a',title:'Recovered',backend:'code'}]}));await ui.view.listHint.click();assert.match(ui.view.listHint.textContent,/已更新/);assert.match(ui.view.rowsRoot.textContent,/Recovered/);
 assert.ok(ui.calls.slice(before).every(c=>c.url.endsWith('/list')&&!c.body));
});
test('manual refresh retains current search, filters, selected chat, draft and list position',async()=>{
 const ui=await opened();ui.view.input.value='draft';ui.view.search.value='needle';ui.view.listMode.value='project';ui.view.archived.checked=true;ui.view.rowsRoot.scrollTop=80;
 let finish;ui.setHandler(()=>new Promise(resolve=>finish=resolve));const pending=ui.view.listSync.update(true);ui.view.search.value='changed';finish({sessions:[{id:'a',title:'changed result',backend:'code',cwd:'/project',archived:true},{id:'b',title:'needle old filter',backend:'cowork'}]});await pending;
 assert.equal(ui.view.search.value,'changed');assert.equal(ui.view.listMode.value,'project');assert.equal(ui.view.archived.checked,true);assert.equal(ui.view.sid,'a');assert.equal(ui.view.input.value,'draft');assert.equal(ui.view.rowsRoot.scrollTop,80);assert.match(ui.view.rowsRoot.textContent,/changed result/);assert.doesNotMatch(ui.view.rowsRoot.textContent,/old filter/);
});
for(const failure of [false,true])test(`a previous provider refresh ${failure?'failure':'success'} cannot affect a new provider refresh`,async()=>{
 const ui=fixture();await ui.view.choose('claude');let resolveOld,rejectOld,resolveNew;ui.setHandler(url=>url.includes('/claude/')?new Promise((resolve,reject)=>{resolveOld=resolve;rejectOld=reject;}):new Promise(resolve=>resolveNew=resolve));
 const old=ui.view.listSync.update(true),switching=ui.view.choose('deepseek');assert.equal(ui.view.listHint.hidden,true);const current=ui.view.listSync.update(true);assert.equal(ui.view.listSync.busy,true);
 if(failure)rejectOld(Error('old failure'));else resolveOld({sessions:[{id:'old',title:'Old provider'}]});await old;assert.equal(ui.view.listSync.busy,true);assert.match(ui.view.listHint.textContent,/正在刷新/);assert.doesNotMatch(ui.view.rowsRoot.textContent,/Old provider/);
 resolveNew({sessions:[{id:'new',title:'New provider'}]});await Promise.all([switching,current]);assert.equal(ui.view.provider,'deepseek');assert.match(ui.view.rowsRoot.textContent,/New provider/);assert.match(ui.view.listHint.textContent,/已更新/);
});
test('account invalidation cancels manual feedback and rejects old list contents',async()=>{
 const ui=fixture();await ui.view.choose('claude');let finish;ui.setHandler(()=>new Promise(resolve=>finish=resolve));const old=ui.view.listSync.update(true);ui.view.invalidateReads('claude');assert.equal(ui.view.listHint.hidden,true);
 finish({sessions:[{id:'old',title:'Old account',backend:'code'}]});await old;assert.equal(ui.view.listHint.hidden,true);assert.doesNotMatch(ui.view.rowsRoot.textContent,/Old account/);
});

function cliDetail(connectionState='closed'){
 const live=connectionState!=='closed';
 return {connected:true,session:{id:'cli-a',title:'CLI chat',backend:'cli',connectionState,status:connectionState==='active'||connectionState==='awaiting'?'active':'idle',cwd:'/cli-project',model:'claude-model',permissionMode:'default',requests:[]},messages:[{id:'old',role:'assistant',model:'older-model',text:'Saved CLI reply'}],capabilities:{resume:!live,send:live,models:live,permissions:live,stop:live,attachments:false,queue:false,steer:false}};
}
const cliClient={id:'claude',enabled:true,installed:true,connected:false,cliAvailable:true,setupStatus:'needs-initialization'};

test('Claude CLI history is readable without initializing Desktop and resumes only on explicit click',async()=>{
 const ui=fixture();let detail=cliDetail();
 ui.setHandler(async(url,body)=>{if(body){assert.match(url,/\/resume$/);assert.equal(body.sessionId,'cli-a');detail=cliDetail('ready');return {status:'ready'};}return url.includes('/detail')?detail:{connected:true,sessions:[detail.session]};});
 ui.view.setClientStates([cliClient]);await ui.view.choose('claude');await ui.view.open('cli-a');
 assert.match(ui.view.messages.textContent,/Saved CLI reply/);assert.equal(ui.calls.filter(c=>c.body).length,0);
 assert.equal(ui.view.send.disabled,true);assert.equal(ui.view.status.textContent,'历史记录');assert.equal(ui.view.resumeButton.hidden,false);
 // Unchanged lifecycle polling must retain the freshly read, closed CLI chat.
 ui.view.setClientStates([cliClient]);assert.equal(ui.view.canResume(),true);assert.equal(ui.view.resumeButton.hidden,false);
 await ui.view.resumeButton.click();await new Promise(setImmediate);
 assert.equal(ui.calls.filter(c=>c.body).length,1);assert.equal(ui.view.send.disabled,false);assert.equal(ui.view.resumeButton.hidden,true);
 assert.equal(ui.view.modelButton.disabled,false);assert.equal(ui.view.permissionsButton.disabled,false);
});

test('cached or disabled Claude CLI details never authorize resume or send',async()=>{
 const ui=fixture();ui.view.provider='claude';ui.view.sid='cli-a';ui.view.setClientStates([cliClient]);
 ui.view.applyDetail(cliDetail(),true);assert.equal(ui.view.canResume(),false);await ui.view.mutate('resume',{});
 ui.view.applyDetail(cliDetail('ready'),true);assert.equal(ui.view.canMutate(),false);await ui.view.mutate('send',{text:'blocked'});
 ui.view.setClientStates([{...cliClient,enabled:false}]);ui.view.applyDetail(cliDetail());await ui.view.mutate('resume',{});
 assert.equal(ui.calls.filter(c=>c.body).length,0);assert.equal(ui.view.send.disabled,true);
});

test('Desktop disconnect does not disable a separately connected Claude CLI chat',async()=>{
 const ui=fixture();let detail=cliDetail('ready');ui.setHandler(async url=>url.includes('/detail')?detail:{connected:true,sessions:[detail.session]});
 ui.view.setClientStates([{...cliClient,connected:true}]);await ui.view.choose('claude');await ui.view.open('cli-a');
 ui.view.setClientStates([cliClient]);assert.equal(ui.view.canMutate(),true);assert.equal(ui.view.send.disabled,false);
 detail={...detail,session:{...detail.session,backend:'code'}};ui.view.applyDetail(detail);assert.equal(ui.view.canMutate(),false);assert.equal(ui.view.send.disabled,true);
});

test('Claude list identifies Code, Cowork and CLI beside each title with metadata below',async()=>{
 const ui=fixture();ui.view.provider='claude';ui.view.rows=[{id:'a',backend:'code',title:'Desktop code',updatedAt:1},{id:'b',backend:'cowork',title:'Desktop cowork',updatedAt:3},{id:'c',backend:'cli',title:'Terminal session',updatedAt:2}];
 ui.view.renderList();const rows=ui.view.rowsRoot.querySelectorAll('.session');assert.deepEqual(rows.map(n=>n.dataset.id),['b','c','a']);assert.deepEqual(rows.map(n=>n.querySelector('.ds-session-type').textContent),['Cowork','CLI','Code']);for(const row of rows){assert.equal(row.children[0].tagName,'STRONG');assert.equal(row.children[1].className,'ds-session-type');assert.equal(row.children[2].tagName,'SMALL');assert.ok(row.children[2].querySelector('time'));}
 ui.run("BridgeI18n.setLanguage('en')");ui.view.sid='c';ui.view.applyDetail(cliDetail());assert.match(ui.view.notice.textContent,/Read the saved history/);assert.equal(ui.view.resumeButton.textContent,'Continue chat');
});

test('Claude CLI approval and stop use current native request and session despite Desktop being offline',async()=>{
 const ui=fixture();const detail=cliDetail('awaiting');detail.session.requests=[{id:'native-request-1',tool:'Bash',input:{command:'synthetic command'}}];
 ui.setHandler(async(url,body)=>body?{status:'accepted'}:url.includes('/detail')?detail:{connected:true,sessions:[detail.session]});
 ui.view.setClientStates([cliClient]);await ui.view.choose('claude');await ui.view.open('cli-a');
 assert.match(ui.view.requests.textContent,/synthetic command/);assert.equal(ui.view.stop.hidden,false);assert.equal(ui.view.stop.disabled,false);
 await ui.view.mutate('respond',{requestId:'native-request-1',decision:'decline'});await new Promise(setImmediate);
 await ui.view.stop.click();const writes=ui.calls.filter(c=>c.body);assert.equal(writes.length,2);assert.equal(writes[0].body.requestId,'native-request-1');assert.match(writes[1].url,/\/stop$/);assert.equal(writes[1].body.sessionId,'cli-a');
});


test('known preflight rejection permits a new explicit Claude CLI resume attempt',async()=>{
 const ui=fixture();let attempts=0,detail=cliDetail();
 ui.setHandler(async(url,body)=>{if(body){attempts++;if(attempts===1)return {status:'blocked',message:'Original terminal is open'};detail=cliDetail('ready');return {status:'ready'};}return url.includes('/detail')?detail:{connected:true,sessions:[detail.session]};});
 ui.view.setClientStates([cliClient]);await ui.view.choose('claude');await ui.view.open('cli-a');
 await ui.view.resumeButton.click();await new Promise(setImmediate);
 assert.equal(attempts,1);assert.equal(ui.view.canResume(),true);assert.equal(ui.view.send.disabled,true);
 await ui.view.resumeButton.click();await new Promise(setImmediate);
 const writes=ui.calls.filter(c=>c.body);assert.equal(writes.length,2);assert.notEqual(writes[0].body.id,writes[1].body.id);assert.equal(ui.view.canMutate(),true);
});

test('Claude directory creation is scoped to the selected SSH environment and sends only after explicit submit',async()=>{
 const ui=fixture();await ui.view.choose('claude');ui.run('window.BridgeProjectPicker={choose(options){window.fixturePicker=options;return Promise.resolve();}}');
 const local={key:'local-cli',surface:'cli',host:'local',name:'Local CLI',createRequiresMessage:true},ssh={key:'ssh-cli',surface:'cli',host:'ssh:work',name:'Work server',createRequiresMessage:true};
 ui.setHandler(async(url,body)=>url.includes('/projects/register')?{project:{key:'registered-ssh',environment:ssh.key,surface:'cli',cwd:'/srv/new-project',name:'new-project'}}:url.includes('/projects/browse')?{path:'/srv',directories:[],roots:[]}:url.includes('/projects')?{projects:[],environments:[local,ssh]}:body?{status:'ready',result:{id:'created'}}:url.includes('/detail')?{session:{id:'created',title:'Native CLI title',status:'idle'},messages:[]}:{sessions:[]});
 await ui.view.create();const dialog=ui.document.querySelector('dialog'),selects=dialog.querySelectorAll('select');selects[0].value=ssh.key;selects[0].onchange();
 const create=dialog.querySelector('.primary');assert.equal(create.disabled,true);assert.equal(dialog.querySelector('input').parentElement.hidden,true);
 await dialog.querySelector('.project-picker-browse').onclick();assert.equal(ui.calls.some(call=>call.body),false);
 await ui.run("window.fixturePicker.request({path:null,hidden:false,name:null,identity:null})");const browse=ui.calls.at(-1);assert.equal(browse.body.environment,ssh.key);
 ui.run("window.fixturePicker.onSelect({path:'/srv/new-project',identity:'ssh-id'})");assert.equal(create.disabled,false);assert.equal(ui.calls.some(call=>call.url.includes('/projects/register')),false);
 dialog.querySelector('textarea').value='First CLI instruction';await dialog.querySelector('form').onsubmit({preventDefault(){}});
 const registered=ui.calls.find(call=>call.url.includes('/projects/register'));assert.deepEqual(JSON.parse(JSON.stringify(registered.body)),{environment:ssh.key,path:'/srv/new-project',createDirectory:false});
 const sent=ui.calls.find(call=>call.url.endsWith('/create'));assert.equal(sent.body.projectKey,'registered-ssh');assert.equal(sent.body.firstMessage,'First CLI instruction');assert.equal(Object.hasOwn(sent.body,'title'),false);
});

test('a provider with no supported project environments cannot advertise directory creation',async()=>{
 const ui=fixture();await ui.view.choose('deepseek');ui.setHandler(async()=>({projects:[],environments:[]}));await ui.view.create();const dialog=ui.document.querySelector('dialog');assert.equal(dialog.querySelector('.project-picker-browse').hidden,true);assert.equal(dialog.querySelector('.primary').disabled,true);assert.equal(ui.calls.some(call=>call.body),false);
});


test('Claude WSL rows and project groups preserve distribution identity at the same path',async()=>{
 const ui=fixture();ui.setHandler(async()=>({sessions:[{id:'a',title:'WSL A',backend:'cli',runtime:'wsl',host:'wsl-claude:a',hostLabel:'Ubuntu A',cwd:'/home/user/project'},{id:'b',title:'WSL B',backend:'cli',runtime:'wsl',host:'wsl-claude:b',hostLabel:'Ubuntu B',cwd:'/home/user/project'}]}));
 await ui.view.choose('claude');assert.match(ui.view.rowsRoot.textContent,/CLI · WSL/);assert.match(ui.view.rowsRoot.textContent,/Ubuntu A/);assert.match(ui.view.rowsRoot.textContent,/Ubuntu B/);
 ui.view.listMode.value='project';ui.view.renderList();assert.equal(ui.view.rowsRoot.querySelectorAll('details').length,2);
});

async function quoteChat(provider='claude'){
 const ui=fixture();await ui.view.choose(provider);await ui.view.open('a');ui.view.bindDraftGateway('gateway-a');
 ui.view.applyDetail({session:{id:'a',title:'Chat',status:'idle'},draftAccountIdentity:'account-a',capabilities:{send:true},messages:[{id:'reply',role:'assistant',text:'Selected answer\nSecond line'}]});return ui;
}

test('selected assistant text becomes a removable quote without editing draft or intercepting native selection',async()=>{
 const ui=await quoteChat(),view=ui.view;view.input.value='my draft';view.input.setSelectionRange(2,4);view.input.oninput();
 const body=view.messages.querySelector('.message-body');ui.document.getSelection=()=>({isCollapsed:false,rangeCount:1,getRangeAt:()=>({startContainer:body,endContainer:body}),toString:()=>'Selected answer'});
 ui.document.dispatchEvent({type:'selectionchange'});assert.equal(view.quotes.action.hidden,false);
 let cancelled=false;view.quotes.action.onpointerdown({preventDefault(){cancelled=true;}});ui.document.getSelection=()=>null;ui.document.activeElement=null;ui.document.dispatchEvent({type:'selectionchange'});assert.equal(view.quotes.action.hidden,false);view.quotes.action.click();
 assert.equal(cancelled,false);assert.equal(view.input.value,'my draft');assert.equal(view.input.selectionStart,2);assert.equal(view.input.selectionEnd,4);assert.equal(view.quotes.quotes[0].text,'Selected answer');assert.equal(view.quotes.quotes[0].provider,'claude');assert.ok(view.quotes.quotes[0].source);
 assert.equal(view.quotes.root.querySelector('details').querySelector('div').textContent,'Selected answer');
 view.quotes.root.querySelector('.composer-quote').querySelector('button').click();assert.equal(view.quotes.quotes.length,0);assert.equal(view.composerDrafts.load(view.draftScope).quotes.length,0);assert.equal(ui.calls.filter(call=>call.body).length,0);
});

test('quote-only drafts restore per provider and session and serialize only on explicit submit',async()=>{
 for(const provider of ['claude']){
  const ui=await quoteChat(provider),view=ui.view;view.quotes.add('A\nB','reply');const original=view.draftScope;
  assert.equal(view.input.value,'');assert.equal(view.composerDrafts.load(original).quotes.length,1);assert.equal(ui.calls.filter(call=>call.body).length,0);
  view.prepareChat('b');view.bindDraftScope({draftAccountIdentity:'account-a'});assert.equal(view.quotes.quotes.length,0);
  view.prepareChat('a');view.applyDetail({session:{id:'a',status:'idle'},draftAccountIdentity:'account-a',capabilities:{send:true},messages:[]});assert.equal(view.quotes.quotes[0].text,'A\nB');
  await view.submit();const sent=ui.calls.filter(call=>call.body);assert.equal(sent.length,1);assert.equal(sent[0].body.text,'> A\n> B');assert.equal(view.quotes.quotes.length,0);assert.equal(view.composerDrafts.load(original),null);
 }
});

test('unknown quote send keeps the same id and new draft revisions survive an earlier acknowledgement',async()=>{
 const ui=await quoteChat(),view=ui.view;view.input.value='draft';view.input.oninput();view.quotes.add('quote','reply');
 ui.setHandler(async(url,body)=>body?{status:'unknown'}:{sessions:[]});await view.submit();const first=ui.calls.filter(call=>call.body)[0].body;
 assert.equal(first.text,'> quote\n\ndraft');assert.equal(view.quotes.quotes.length,1);assert.equal(view.input.value,'draft');
 view.freshDetail=true;let resolve;ui.setHandler((url,body)=>body?new Promise(done=>resolve=done):Promise.resolve({sessions:[]}));const retry=view.submit();view.quotes.add('later','reply-2');resolve({status:'accepted'});await retry;
 assert.equal(ui.calls.filter(call=>call.body)[1].body.id,first.id);assert.equal(view.quotes.quotes.length,2);assert.equal(view.input.value,'draft');assert.equal(view.composerDrafts.load(view.draftScope).quotes.length,2);
});

test('followup prompt events append editable text only in the connected current assistant message',async()=>{
 const ui=await quoteChat(),view=ui.view;view.input.value='keep';view.input.setSelectionRange(1,3);view.input.oninput();const body=view.messages.querySelector('.message-body'),button=ui.document.createElement('button');body.append(button);
 view.messages['onbridge-compose-prompt']({target:button,detail:{text:'Follow up'}});assert.equal(view.input.value,'keep\n\nFollow up');assert.equal(view.input.selectionStart,1);assert.equal(view.input.selectionEnd,3);assert.equal(ui.calls.filter(call=>call.body).length,0);
 view.prepareChat('b');view.bindDraftScope({draftAccountIdentity:'account-a'});view.messages['onbridge-compose-prompt']({target:button,detail:{text:'stale'}});assert.equal(view.input.value,'');
 view.clear();view.messages['onbridge-compose-prompt']({target:button,detail:{text:'logged out'}});assert.equal(view.quotes.quotes.length,0);assert.equal(view.input.value,'');
});

test('cross-message and user selections and detached streaming reply snapshots cannot become quotes',async()=>{
 const ui=await quoteChat(),view=ui.view,body=view.messages.querySelector('.message-body'),other=ui.document.createElement('div');view.messages.append(other);
 ui.document.getSelection=()=>({isCollapsed:false,rangeCount:1,getRangeAt:()=>({startContainer:body,endContainer:other}),toString:()=>'cross boundary'});view.quotes.capture();assert.equal(view.quotes.action.hidden,true);
 ui.document.getSelection=()=>({isCollapsed:false,rangeCount:1,getRangeAt:()=>({startContainer:body,endContainer:body}),toString:()=>'snapshot'});view.quotes.capture();view.quotes.action.onpointerdown();body.remove();view.quotes.action.click();assert.equal(view.quotes.quotes.length,0);
 const user=ui.document.createElement('article');user.className='message user';const userBody=ui.document.createElement('div');userBody.className='message-body';user.append(userBody);view.messages.append(user);ui.document.getSelection=()=>({isCollapsed:false,rangeCount:1,getRangeAt:()=>({startContainer:userBody,endContainer:userBody}),toString:()=>'user text'});view.quotes.capture();assert.equal(view.quotes.action.hidden,true);
});

test('combined quote and draft limit blocks a send without truncating or clearing either',async()=>{
 const ui=await quoteChat(),view=ui.view;view.input.value='x'.repeat(99999);view.input.oninput();view.quotes.add('quote');await view.submit();assert.match(view.error.textContent,/100000/);assert.equal(ui.calls.filter(call=>call.body).length,0);assert.equal(view.input.value.length,99999);assert.equal(view.quotes.quotes.length,1);
});

test('failed durable storage cannot let an acknowledgement erase a later quote-only edit',async()=>{
 for(const provider of ['claude']){
  const ui=await quoteChat(provider),view=ui.view;ui.run("localStorage.setItem=()=>{throw Error('quota');}");view.quotes.add('first','reply');
  let resolve;ui.setHandler((url,body)=>body?new Promise(done=>resolve=done):Promise.resolve(url.includes('/detail')?{session:{id:'a',status:'idle'},draftAccountIdentity:'account-a',capabilities:{send:true},messages:[]}:{sessions:[]}));const sending=view.submit();assert.equal(view.composerDrafts.pending(view.draftScope),null);
  view.quotes.add('later','reply-2');assert.equal(view.input.value,'');resolve({status:'accepted'});await sending;
  assert.equal(view.input.value,'');assert.deepEqual(Array.from(view.quotes.quotes,row=>row.text),['first','later']);assert.deepEqual(Array.from(view.provisionalDrafts.get(provider+':a').quotes,row=>row.text),['first','later']);
 }
});


test('message quote uses complete text and rejects late results after draft identity changes',async()=>{
 const ui=await quoteChat(),view=ui.view,node=view.messages.querySelector('.message');view.input.value='keep';view.input.oninput();
 view.messages['onbridge-compose-quote']({target:node,detail:{getText:()=>Promise.resolve('complete answer'),source:'reply'}});await new Promise(setImmediate);
 assert.equal(view.quotes.quotes[0].text,'complete answer');assert.equal(view.input.value,'keep');assert.equal(ui.calls.filter(call=>call.body).length,0);
 let finish;const pending=view.quotes.quoteMessage(()=>new Promise(resolve=>finish=resolve),'reply',node);
 view.prepareChat('b');view.bindDraftScope({draftAccountIdentity:'account-a'});finish('stale answer');await pending;assert.equal(view.quotes.quotes.length,0);
});

test('all desktop clients reuse text quotes and editable followup prompts without sending',async()=>{
 for(const provider of ['claude','deepseek','zcode']){
  const ui=await quoteChat(provider),view=ui.view,body=view.messages.querySelector('.message-body');
  assert.equal(view.messages.querySelector('.message-actions').children.length,2);assert.equal(view.quotes.available(),true);
  view.quotes.add('quoted answer','reply');assert.equal(view.quotes.quotes.length,1);
  ui.document.getSelection=()=>({isCollapsed:false,rangeCount:1,getRangeAt:()=>({startContainer:body,endContainer:body}),toString:()=>'Selected answer'});view.quotes.capture();assert.equal(view.quotes.action.hidden,false);
  const button=ui.document.createElement('button');body.append(button);view.messages['onbridge-compose-prompt']({target:button,detail:{text:'Follow up'}});assert.equal(view.input.value,'Follow up');assert.equal(ui.calls.filter(call=>call.body).length,0);
  assert.equal(view.quotes.serialize(view.input.value),'> quoted answer\n\nFollow up');
 }
});

test('last-prompt counters never become a current context percentage',async()=>{
 const ui=await opened();
 ui.view.applyDetail({session:{id:'a',title:'A',contextUsage:null,contextUsageStatus:'unavailable',lastPromptContextUsage:{usedTokens:112973,contextWindow:200000,measurement:'lastPrompt'}},messages:[]});
 assert.ok(ui.view.contextRing.classList.contains('unknown'));assert.doesNotMatch(ui.view.contextButton.title,/56%/);
 ui.view.contextDetails();const dialog=ui.document.querySelector('dialog');assert.match(dialog.textContent,/112,973/);assert.match(dialog.textContent,/最近一次模型输入/);assert.doesNotMatch(dialog.textContent,/56\.5%/);dialog.close();
 ui.view.applyDetail({session:{id:'a',title:'A',contextUsage:{usedTokens:112973,contextWindow:200000,measurement:'lastPrompt'}},messages:[]});
 assert.ok(ui.view.contextRing.classList.contains('unknown'));
 ui.view.applyDetail({session:{id:'a',title:'A',contextUsage:{usedTokens:67500,contextWindow:200000}},messages:[]});assert.match(ui.view.contextButton.title,/34%/);
});

test('native branch actions require capability and canonical message identity and preserve source draft',async()=>{
 const ui=await opened(),message={id:'display-id',role:'assistant',text:'Answer',forkMessageId:'native-uuid'};
 ui.view.applyDetail({session:{id:'a',title:'A'},capabilities:{fork:false},messages:[message]});
 assert.equal(ui.view.messages.querySelectorAll('button').some(b=>b.textContent==='从这里分支'),false);
 ui.view.applyDetail({session:{id:'a',title:'A'},capabilities:{fork:true},messages:[{...message,forkMessageId:null}]});
 assert.equal(ui.view.messages.querySelectorAll('button').some(b=>b.textContent==='从这里分支'),false);
 ui.view.applyDetail({session:{id:'a',title:'A'},capabilities:{fork:true},messages:[message]});
 ui.view.input.value='source draft';let saved=0,openedChild;ui.view.saveDraft=()=>{saved++;};ui.view.open=async id=>{openedChild=id;};
 ui.setHandler(async(url,body)=>body?{status:'ready',result:{id:'child'}}:{sessions:[]});
 await ui.view.messages.querySelectorAll('button').find(b=>b.textContent==='从这里分支').click();
 const branch=ui.calls.find(c=>c.url.endsWith('/fork'));assert.equal(branch.body.sessionId,'a');assert.equal(branch.body.messageId,'native-uuid');assert.equal(Object.hasOwn(branch.body,'text'),false);assert.equal(openedChild,'child');assert.ok(saved>0);assert.equal(ui.view.input.value,'source draft');
});

test('uncertain branch retries keep the same receipt and a late result cannot switch chats',async()=>{
 const ui=await opened();ui.view.applyDetail({session:{id:'a',title:'A'},capabilities:{fork:true},messages:[]});ui.view.refresh=async()=>{};let finish;const ids=[];
 ui.setHandler(async(url,body)=>{if(body){ids.push(body.id);return {status:'unknown',message:'Check Desktop'};}return {sessions:[]};});
 await ui.view.fork('native-uuid');ui.view.applyDetail({session:{id:'a',title:'A'},capabilities:{fork:true},messages:[]});await ui.view.fork('native-uuid');assert.equal(ids.length,2);assert.equal(ids[0],ids[1]);
 ui.view.applyDetail({session:{id:'a',title:'A'},capabilities:{fork:true},messages:[]});let selected;ui.view.open=async id=>{selected=id;};ui.setHandler((url,body)=>body?new Promise(resolve=>finish=resolve):Promise.resolve({sessions:[]}));
 const branch=ui.view.fork('other-native-uuid');ui.view.sid='b';finish({status:'ready',result:{id:'child'}});await branch;assert.equal(selected,undefined);
});

test('Claude trust refusal preserves creation fields and explicit retry uses a new receipt',async()=>{
 const ui=await opened(),ids=[];let attempts=0;
 ui.setHandler(async(url,body)=>{
  if(url.includes('/projects')&&!body)return {projects:[{key:'p',surface:'code',cwd:'/trusted-later',name:'Project'}],environments:[{key:'code',surface:'code',createRequiresMessage:true}]};
  if(url.endsWith('/create')){ids.push(body.id);attempts++;return {status:'blocked',code:'workspace-trust-required',message:'请先在 Claude Desktop 的 Code 页面打开此目录并确认“信任工作区”，再返回重试。首条消息已保留。'};}
  return {sessions:[]};
 });
 await ui.view.create();const dialog=ui.document.querySelector('dialog'),form=dialog.querySelector('form'),message=dialog.querySelector('textarea'),title=dialog.querySelector('input');title.value='Preserved title';message.value='Preserved first instruction';
 await form.onsubmit({preventDefault(){}});assert.equal(dialog.open,true);assert.match(dialog.textContent,/信任工作区/);assert.equal(title.value,'Preserved title');assert.equal(message.value,'Preserved first instruction');
 await form.onsubmit({preventDefault(){}});assert.equal(attempts,2);assert.notEqual(ids[0],ids[1]);assert.equal(message.value,'Preserved first instruction');
});

test('verified WSL account continuation preserves same-session text selection and attachments without sending',async()=>{
 const ui=await opened(),view=ui.view,entry='wsl-claude:'+ 'a'.repeat(32),previous='1'.repeat(64),next='2'.repeat(64);view.bindDraftGateway('gateway-a');view.bindDraftScope({draftAccountIdentity:previous});view.rows=[{id:'a',host:entry,runtime:'wsl'}];view.input.value='retained text';view.input.setSelectionRange(2,7);view.saveDraft();
 view.attachments.rows.push({id:'attachment-a',name:'file.txt',size:10,status:'ready'});const attachmentKey=view.attachments.key;view.attachments.save(attachmentKey,view.attachments.rows);const savedAttachments=ui.sessionStore.get('attachments:'+attachmentKey),calls=ui.calls.filter(call=>call.body).length;
 const result={provider:'claude',entry,draftContinuations:[{id:'a',previousAccountIdentity:previous,accountIdentity:next}]};assert.equal(view.continueWslAccountDrafts(result),1);assert.equal(view.input.value,'retained text');assert.equal(view.input.selectionStart,2);assert.equal(view.input.selectionEnd,7);assert.equal(view.draftScope.account,next);assert.equal(view.attachments.key,attachmentKey);assert.equal(view.attachments.rows[0].id,'attachment-a');assert.equal(ui.sessionStore.get('attachments:'+attachmentKey),savedAttachments);assert.equal(ui.calls.filter(call=>call.body).length,calls);assert.equal(view.pending.size,0);
});
test('WSL draft continuation rejects another provider host account and unresolved submissions',async()=>{
 const ui=await opened(),view=ui.view,entry='wsl-claude:'+ 'a'.repeat(32),previous='1'.repeat(64),next='2'.repeat(64);view.bindDraftGateway('gateway-a');view.bindDraftScope({draftAccountIdentity:previous});view.rows=[{id:'a',host:entry,runtime:'wsl'}];view.input.value='private draft';view.saveDraft();const result={provider:'claude',entry,draftContinuations:[{id:'a',previousAccountIdentity:previous,accountIdentity:next}]};
 for(const changed of [{provider:'codex'},{entry:'wsl-claude:'+'b'.repeat(32)},{continuationPending:true},{draftContinuations:[{id:'a',previousAccountIdentity:'3'.repeat(64),accountIdentity:next}]}])assert.equal(view.continueWslAccountDrafts({...result,...changed}),0);
 const pendingKey=view.pendingKey('send',{text:'private draft'},'a','claude',previous);view.pending.set(pendingKey,'unknown-id');assert.equal(view.continueWslAccountDrafts(result),0);assert.equal(view.pending.get(pendingKey),'unknown-id');assert.equal(view.draftScope.account,previous);assert.equal(view.composerDrafts.load({...view.draftScope,account:next}),null);
});

test('fresh verified WSL detail continues an offline mobile draft after a switch from another client',async()=>{
 const ui=await opened(),view=ui.view,entry='wsl-claude:'+'a'.repeat(32),old='1'.repeat(64),middle='2'.repeat(64),current='3'.repeat(64);view.bindDraftGateway('gateway-a');
 const previous={gateway:'gateway-a',provider:'claude',account:old,host:'local',session:'a'};view.composerDrafts.save(previous,{text:'offline phone draft',selectionStart:3,selectionEnd:9});
 const hint={id:'a',entry,accountIdentity:current,previousAccountIdentities:[old,middle]},value={session:{id:'a',runtime:'wsl',host:entry,draftAccountIdentity:current},draftContinuation:hint};
 const calls=ui.calls.filter(call=>call.body).length;view.bindDraftScope(value);assert.equal(view.input.value,'offline phone draft');assert.equal(view.input.selectionStart,3);assert.equal(view.draftScope.account,current);assert.equal(ui.calls.filter(call=>call.body).length,calls);
});
test('unverified external account changes and other WSL entries cannot use draft lineage',async()=>{
 const ui=await opened(),view=ui.view,entry='wsl-claude:'+'a'.repeat(32),old='1'.repeat(64),current='2'.repeat(64);view.bindDraftGateway('gateway-a');const previous={gateway:'gateway-a',provider:'claude',account:old,host:'local',session:'a'};view.composerDrafts.save(previous,{text:'private old draft'});
 const session={id:'a',runtime:'wsl',host:entry,draftAccountIdentity:current},hint={id:'a',entry,accountIdentity:current,previousAccountIdentities:[old]};
 for(const value of [{session},{session,draftContinuation:{...hint,entry:'wsl-claude:'+'b'.repeat(32)}},{session,draftContinuation:{...hint,id:'other'}},{session,draftContinuation:{...hint,accountIdentity:'3'.repeat(64)}}]){view.bindDraftScope(value);assert.equal(view.input.value,'');assert.notEqual(view.composerDrafts.load({...previous,account:current})?.text,'private old draft');}
 view.composerDrafts.begin(previous,{text:'private old draft'},'unknown-original');view.bindDraftScope({session,draftContinuation:hint});assert.notEqual(view.composerDrafts.load({...previous,account:current})?.text,'private old draft');assert.equal(view.composerDrafts.pending(previous).id,'unknown-original');assert.equal(view.input.value,'');
});

test('mobile draft continuation consumes manager-wrapped account identities while session keeps raw native ownership',async()=>{
 const ui=await opened(),view=ui.view,entry='wsl-claude:'+'a'.repeat(32),rawOld='1'.repeat(64),rawCurrent='2'.repeat(64),wrap=raw=>require('node:crypto').createHash('sha256').update(JSON.stringify(['draft-v1','claude','wsl-cli',entry,raw])).digest('hex'),old=wrap(rawOld),current=wrap(rawCurrent);view.bindDraftGateway('gateway-a');view.composerDrafts.save({gateway:'gateway-a',provider:'claude',account:old,host:'local',session:'a'},{text:'real wrapped scope draft'});view.bindDraftScope({draftAccountIdentity:current,session:{id:'a',runtime:'wsl',host:entry,draftAccountIdentity:rawCurrent},draftContinuation:{id:'a',entry,accountIdentity:current,previousAccountIdentities:[old]}});assert.notEqual(current,rawCurrent);assert.equal(view.input.value,'real wrapped scope draft');assert.equal(view.draftScope.account,current);
});

test('common chat demand waits for resources and Back cancels the pending detail read',async()=>{
 const ui=fixture();await ui.view.choose('claude');
 ui.run("var releaseChat;window.BridgeFeatures={load:()=>new Promise(resolve=>{releaseChat=resolve;})}");
 const before=ui.calls.filter(call=>call.url.includes('/detail')).length;
 const opening=ui.view.open('a');assert.equal(ui.view.sid,'a');
 assert.equal(ui.calls.filter(call=>call.url.includes('/detail')).length,before);
 ui.view.backToList();ui.run('releaseChat()');await opening;
 assert.equal(ui.view.sid,null);assert.equal(ui.calls.filter(call=>call.url.includes('/detail')).length,before);
});

test('only the latest chat receives detail after shared renderer loading completes',async()=>{
 const ui=fixture();await ui.view.choose('claude');
 ui.run("var releaseChat;var chatReady=new Promise(resolve=>{releaseChat=resolve;});window.BridgeFeatures={load:()=>chatReady}");
 const before=ui.calls.filter(call=>call.url.includes('/detail')).length;
 const first=ui.view.open('a'),second=ui.view.open('b');ui.run('releaseChat()');await Promise.all([first,second]);
 const reads=ui.calls.filter(call=>call.url.includes('/detail')).slice(before);
 assert.equal(reads.length,1);assert.match(reads[0].url,/sessionId=b/);
});

test('Claude and DSH recent pins paint pending local state while project order stays unchanged',async()=>{
 for(const provider of ['claude','deepseek']){
  const ui=fixture(),menus=[];await ui.view.choose(provider);
  ui.view.pinPreferences={value:(identity,server)=>identity.id==='old'||!!server,pending:identity=>identity.id==='old'};
  ui.view.onSessionMenu=(button,row,kind)=>menus.push({button,row,kind});
  ui.view.rows=[{id:'new',title:'New',cwd:'/project',updatedAt:30},{id:'old',title:'Old',cwd:'/project',updatedAt:1}];ui.view.listMode.value='recent';ui.view.renderList();
  assert.deepEqual(ui.view.rowsRoot.querySelectorAll('.session').map(n=>n.dataset.id),['old','new']);
  assert.match(ui.view.rowsRoot.textContent,/置顶设置待同步|Pin preference pending sync/);assert.equal(menus[0].kind,provider);assert.equal(menus[0].row.bridgePinned,true);
  ui.view.listMode.value='project';ui.view.renderList();assert.deepEqual(ui.view.rowsRoot.querySelectorAll('.session').map(n=>n.dataset.id),['new','old']);
 }
});

for(const provider of ['claude','deepseek'])for(const prefetched of [false,true])test(`${provider} successful list with local pin sync keeps its read token (prefetched=${prefetched})`,async()=>{
 const ui=fixture();await ui.view.choose(provider);const token={scope:'gateway',generation:1,epoch:7},observed=[];
 ui.view.pinPreferences={capture:()=>token,observe:(...args)=>observed.push(args),value:(_identity,value)=>!!value,pending:()=>false};
 ui.setHandler(async()=>({connected:true,complete:true,sessions:[{id:'pinned-chat',title:'Pinned chat',bridgePinned:true}]}));
 if(prefetched){ui.view.sharedRead(provider,'list');ui.view.pinPreferences.capture=()=>({...token,epoch:8});}
 assert.equal(await ui.view.refreshList(),true);
 assert.equal(observed.length,1);assert.equal(observed[0][2],token);assert.equal(observed[0][0].provider,provider);assert.equal(observed[0][0].id,'pinned-chat');assert.equal(observed[0][1],true);
 assert.match(ui.view.rowsRoot.textContent,/Pinned chat/);assert.equal(ui.view.listSync.automaticFailures,0);
});

test('explicit Bridge project and section grouping preserves execution location',async()=>{
 const ui=fixture();await ui.view.choose('claude');ui.view.listMode.value='project';
 ui.view.rows=[{id:'a',title:'A',backend:'cli',cwd:'/original',projectId:'p',projectName:'Engineering',sectionId:'s',sectionName:'Todo'},
 {id:'b',title:'B',backend:'cli',cwd:'/original',projectId:'p',projectName:'Engineering',sectionId:'s2',sectionName:'Review'}];
 ui.view.renderList();assert.equal(ui.view.rowsRoot.querySelectorAll('details').length,2);
 assert.match(ui.view.rowsRoot.textContent,/Engineering.*Todo/);assert.match(ui.view.rowsRoot.textContent,/Review/);
 assert.ok(ui.view.rows.every(row=>row.cwd==='/original'));
});

for(const [provider,backend,runtime] of [['claude','code','local'],['claude','cowork','local'],['claude','cli','local'],['claude','cli','wsl'],['deepseek','desktop','local']])test(`${provider} ${backend} ${runtime} send shows pending content synchronously and confirms only after acknowledgement`,async()=>{
 const ui=fixture(),view=ui.view;await view.choose(provider);await view.open('a');view.state={...view.state,backend,runtime,connectionState:'ready'};
 let finish;ui.setHandler((url,body)=>body?new Promise(resolve=>finish=resolve):Promise.resolve(url.includes('/detail')?{session:{id:'a',status:'idle',capabilities:{}},messages:[{id:'native',role:'user',text:'slow message'}]}:{sessions:[]}));
 view.input.value='slow message';const sending=view.submit();
 assert.equal(view.sendFeedback.hidden,false);assert.match(view.sendFeedback.textContent,/正在发送….*slow message/);assert.equal(view.send.textContent,'正在发送…');assert.equal(view.send.disabled,true);assert.equal(ui.notices.length,0);
 await view.submit();assert.equal(ui.calls.filter(call=>call.body).length,1,'a second click does not submit twice');
 view.applyDetail({session:{id:'a',status:'idle',capabilities:{}},messages:[{id:'native',role:'user',text:'slow message'}]});assert.equal(view.messages.querySelectorAll('.message').length,1,'pending preview is not a second native-history bubble');assert.equal(view.sendFeedback.hidden,false);
 view.input.value='next draft';view.input.oninput();finish({status:'accepted'});await sending;
 assert.equal(view.sendFeedback.hidden,true);assert.equal(view.send.textContent,'发送 ↑');assert.equal(view.input.value,'next draft');assert.deepEqual(ui.notices,['已发送到会话']);
});

test('queued send confirmation differs from delivery and failures keep the original draft and request id',async()=>{
 const ui=await opened(),view=ui.view;let writes=0,first;ui.setHandler(async(url,body)=>{if(!body)return url.includes('/detail')?{session:{id:'a',status:'idle',capabilities:{}},messages:[]}:{sessions:[]};writes++;first??=body.id;assert.equal(body.id,first);return writes===1?{status:'unknown',message:'结果待核对'}:{status:'queued'};});
 view.input.value='keep me';await view.submit();assert.equal(view.sendFeedback.hidden,true);assert.equal(view.input.value,'keep me');assert.deepEqual(ui.notices,['结果待核对']);assert.equal(writes,1);
 view.freshDetail=true;await view.submit();assert.equal(writes,2);assert.equal(view.input.value,'');assert.equal(ui.notices.at(-1),'已加入待发送队列');
});

test('pending send feedback stays scoped across chat, provider and account changes',async()=>{
 for(const change of ['chat','provider','account','gateway','logout']){
  const ui=await opened(),view=ui.view;view.bindDraftGateway('gateway-a');view.bindDraftScope({draftAccountIdentity:'account-a'});let finish;
  ui.setHandler((url,body)=>body?new Promise(resolve=>finish=resolve):Promise.resolve({sessions:[]}));view.input.value='private pending text';const sending=view.submit();assert.equal(view.sendFeedback.hidden,false);
  if(change==='chat')view.sid='b';else if(change==='provider')view.provider='deepseek';else if(change==='account')view.bindDraftScope({draftAccountIdentity:'account-b'});else if(change==='gateway')view.bindDraftGateway('gateway-b');else view.clear();
  view.updateComposer();assert.equal(view.sendFeedback.hidden,true,change);assert.equal(view.sendFeedback.textContent,'',change+' removes hidden private text');
  finish({status:'accepted'});await sending;assert.equal(view.sendFeedback.hidden,true);assert.equal(ui.notices.length,0,change+' does not announce success in another context');
 }
});

test('network failure clears only transient sending UI and never retries or clears the draft',async()=>{
 const ui=await opened(),view=ui.view;ui.setHandler(async(url,body)=>{if(body)throw Error('connection lost');return {sessions:[]};});view.input.value='preserved after timeout';await view.submit();assert.equal(view.sendFeedback.hidden,true);assert.equal(view.input.value,'preserved after timeout');assert.equal(ui.calls.filter(call=>call.body).length,1);assert.deepEqual(ui.notices,['connection lost']);assert.equal(view.pending.size,1);
});

test('late send failure does not expose its message after leaving the original provider',async()=>{
 const ui=await opened(),view=ui.view;let reject;ui.setHandler((url,body)=>body?new Promise((resolve,fail)=>reject=fail):Promise.resolve({sessions:[]}));view.input.value='private message';const sending=view.submit();view.provider='deepseek';view.updateComposer();reject(Error('private source error'));await sending;assert.equal(view.error.textContent,'');assert.equal(ui.notices.length,0);
});

test('empty Claude and DSH catalogs retain manual model and effort without fake account switching',async()=>{
 for(const provider of ['claude','deepseek']){
  const ui=fixture();await ui.view.choose(provider);await ui.view.open('a');ui.view.state.modelCatalogIdentity='same-account';ui.view.state.capabilities={models:true,effort:true,manualModels:true,manualEffort:true};
  ui.setHandler(async(url,body)=>body?{status:'accepted'}:{models:[],capabilities:{models:true,manualModels:true,manualEffort:true},cache:{identity:'same-account'}});
  await ui.view.models();const dialog=ui.document.querySelector('dialog'),model=dialog.querySelector('.ds-manual-model'),effort=dialog.querySelector('.ds-manual-effort');
  assert.equal(model.hidden,false);assert.doesNotMatch(dialog.textContent,/muted|账号已切换/);model.value=provider==='deepseek'?'provider/manual-model':'manual-model';model.oninput();
  const custom=dialog.querySelector('.effort-options').querySelector('input');custom.checked=true;custom.onchange();effort.value='high';effort.oninput();
  assert.equal(effort.hidden,false);assert.equal(dialog.querySelector('.primary').disabled,false);await dialog.querySelector('form').onsubmit({preventDefault(){}});
  const write=ui.calls.find(call=>call.url.includes('/settings'));assert.equal(write.body.model,model.value);assert.equal(write.body.effort,'high');assert.equal(write.body.manualEffort,true);
 }
});
test('model catalog account mismatch remains blocked and does not expose manual writes',async()=>{
 const ui=await opened();ui.view.state.modelCatalogIdentity='account-a';ui.view.state.capabilities={models:true,effort:true};ui.setHandler(async()=>({models:[],capabilities:{models:true},cache:{identity:'account-b'}}));await ui.view.models();
 const dialog=ui.document.querySelector('dialog');assert.match(dialog.querySelector('.error').textContent,/账号已切换/);assert.equal(dialog.querySelector('.primary').disabled,true);await dialog.querySelector('form').onsubmit({preventDefault(){}});assert.equal(ui.calls.filter(call=>call.body).length,0);
});

test('older model cache cannot erase verified live manual capability or revive an old DSH runtime',async()=>{
 for(const supported of [true,false]){const ui=fixture();await ui.view.choose('deepseek');await ui.view.open('a');ui.view.state.capabilities={models:true,manualModels:supported,manualEffort:supported};ui.setHandler(async()=>({models:[{id:'model-a',efforts:[]}],capabilities:{models:true,manualModels:!supported,manualEffort:!supported}}));await ui.view.models();const dialog=ui.document.querySelector('dialog');assert.equal(dialog.querySelector('.model-options').querySelectorAll('input').some(input=>input.value==='__custom__'),supported);dialog.close();}
});

test('manual settings while catalog is pending carry the captured account and stale responses cannot replace it',async()=>{
 const ui=await opened();ui.view.state.modelCatalogIdentity='account-a';ui.view.state.capabilities={models:true,manualModels:true,manualEffort:true};let finish;
 ui.setHandler(async(url,body)=>body?{status:'unknown',message:'账号已切换，请重新打开模型设置'}:url.includes('/catalog')?new Promise(resolve=>finish=resolve):{sessions:[]});const opening=ui.view.models();const dialog=ui.document.querySelector('dialog'),input=dialog.querySelector('.ds-manual-model');input.value='manual-model';input.oninput();assert.equal(dialog.querySelector('.primary').disabled,false);await dialog.querySelector('form').onsubmit({preventDefault(){}});const write=ui.calls.find(call=>call.body);assert.equal(write.body.modelCatalogIdentity,'account-a');finish({models:[{id:'private-b'}],cache:{identity:'account-b'}});await opening;assert.doesNotMatch(dialog.textContent,/private-b/);assert.equal(dialog.querySelector('.primary').disabled,true);
});

test('model source notices are translated per line without hiding automatic or manual choices',async()=>{
 const ui=await opened();ui.view.state.modelCatalogIdentity='account';ui.view.state.capabilities={models:true,manualModels:true,manualEffort:true};
 ui.run("BridgeI18n.t=text=>({'配置来源提示':'Configured models','来源尚待核验':'Sources require verification'}[text]||text)");
 ui.setHandler(async()=>({models:[{id:'current-model',source:'upstream-api',efforts:['high']}],source:'mixed',modelSourcesVersion:1,capabilities:{models:true,manualModels:true,manualEffort:true},cache:{identity:'account'},notice:'配置来源提示\n来源尚待核验'}));
 await ui.view.models();const dialog=ui.document.querySelector('dialog');assert.match(dialog.textContent,/Configured models\nSources require verification/);assert.doesNotMatch(dialog.textContent,/历史模型仅供查看/);
 const choices=dialog.querySelector('.model-options').querySelectorAll('input').map(row=>row.value);assert.ok(choices.includes('current-model'));assert.ok(choices.includes('__custom__'));
});

test('project trust requires explicit confirmation and never replays the first message',async()=>{
 const ui=await opened();ui.run('window.BridgeProjectPicker={confirmTrust:async()=>false}');
 ui.setHandler(async(url,body)=>{
  if(url.endsWith('/projects'))return {projects:[{key:'p',surface:'code',environment:'code',cwd:'/selected',name:'Project'}],environments:[{key:'code',surface:'code',createRequiresMessage:true}]};
  if(url.endsWith('/create'))return {status:'blocked',code:'workspace-trust-required',workspace:'/selected',canConfirmTrust:true,message:'Native trust required'};
  if(url.endsWith('/register'))return {project:{key:'registered',cwd:'/selected'}};
  if(url.endsWith('/trust'))return {trusted:body.confirmed,path:'/selected'};
  return {sessions:[]};
 });
 await ui.view.create();const dialog=ui.document.querySelector('dialog'),form=dialog.querySelector('form'),message=dialog.querySelector('textarea');message.value='Retain this instruction';
 await form.onsubmit({preventDefault(){}});const trust=dialog.querySelectorAll('button').find(b=>b.textContent==='确认工作区信任…');assert.equal(trust.hidden,false);
 await trust.onclick();assert.equal(ui.calls.filter(c=>c.url.endsWith('/trust')&&c.body.confirmed).length,0);assert.equal(message.value,'Retain this instruction');
 ui.run('window.BridgeProjectPicker.confirmTrust=async options=>options.isCurrent()');await trust.onclick();
 assert.equal(ui.calls.filter(c=>c.url.endsWith('/trust')&&c.body.confirmed).length,1);assert.equal(ui.calls.filter(c=>c.url.endsWith('/create')).length,1);assert.equal(message.value,'Retain this instruction');assert.equal(trust.hidden,true);
});

 test('shared detached native chat requires explicit resume before mode writes',async()=>{
 const ui=fixture();ui.view.provider='deepseek';ui.view.sid='cold';ui.view.setClientStates([{id:'deepseek',enabled:true,connected:true}]);
 ui.view.applyDetail({session:{id:'cold',status:'idle',connectionState:'detached'},capabilities:{resume:true,plan:false,goal:true},messages:[]});
 assert.equal(ui.view.canMutate(),false);assert.equal(ui.view.canResume(),true);assert.equal(ui.view.send.disabled,true);assert.equal(ui.view.workMode.disabled,true);
 });

test('ZCode chat list offers private-pipe initialization even when saved history is readable',async()=>{
 const ui=fixture(),view=ui.view,requested=[];
 const client={id:'zcode',enabled:true,connected:false,historyAvailable:true,canInitializeDesktop:true,pipeSupported:true,initializationMode:'private-pipe',setupStatus:'history-only'};
 view.provider='zcode';view.setClientStates([client],null,id=>requested.push(id));
 assert.equal(view.connectionInitialize.hidden,false);assert.equal(view.connectionInitialize.textContent,'初始化连接');
 assert.equal(view.renderConnectionStatus(),false);await view.connectionInitialize.onclick();assert.deepEqual(requested,['zcode']);
 view.setClientStates([{...client,setupStatus:'session-access',canInitializeDesktop:false}]);assert.equal(view.connectionInitialize.hidden,true);
});

test('a verified ZCode initialization immediately refreshes readable history for the selected session',async()=>{
 const ui=fixture(),view=ui.view,old={id:'zcode',enabled:true,connected:false,historyAvailable:true,pipeSupported:true,canInitializeDesktop:true,setupStatus:'history-only'};
 view.provider='zcode';view.setClientStates([old]);assert.equal(view.connectionInitialize.textContent,'初始化连接');
 let reads=0;view.refresh=async()=>{reads++;};
 const ready={...old,pipeReady:true,canInitializeDesktop:false,setupStatus:'session-access'};
 await view.setClientStates([ready]);assert.equal(reads,1);assert.equal(view.connectionInitialize.hidden,true);
 await view.setClientStates([ready]);assert.equal(reads,1);
});
test('ZCode native create reuses firstMessage contract and omits hidden title',async()=>{
 const ui=fixture();ui.view.provider='zcode';ui.view.generation=1;
 ui.setHandler(async(url,body)=>url.endsWith('/projects')?{projects:[{key:'p',environment:'desktop',cwd:'/p',name:'Project'}],environments:[{key:'desktop',surface:'desktop',createRequiresMessage:true,createTitle:false}],browseDirectories:false}:url.endsWith('/create')?{status:'accepted',result:{id:'native'}}:url.includes('/detail')?{session:{id:'native',title:'Native',status:'idle'},messages:[]}:{sessions:[]});
 await ui.view.create();const dialog=ui.document.querySelector('dialog'),form=dialog.querySelector('form');dialog.querySelector('input').value='';dialog.querySelector('textarea').value='first message';await form.onsubmit({preventDefault(){}});
 const sent=ui.calls.find(c=>c.url.endsWith('/create')).body;assert.equal(sent.firstMessage,'first message');assert.equal(sent.title,undefined);assert.equal(sent.projectKey,'p');
});

test('all desktop creation surfaces pass the selected model before their first message',async()=>{
 for(const provider of ['claude','deepseek','zcode']){
  const ui=fixture();ui.view.provider=provider;ui.view.generation=1;
  const environment={key:'scope',surface:provider==='claude'?'code':'desktop',host:'local',createRequiresMessage:provider!=='deepseek',createTitle:provider!=='zcode'};
  ui.setHandler(async(url,body)=>url.endsWith('/projects')?{createModels:true,projects:[{key:'p',environment:'scope',surface:environment.surface,host:'local',cwd:'/p',name:'Project'}],environments:[environment],browseDirectories:false}:url.includes('/create-models?')?{models:[{id:'expensive-first',efforts:['high']},{id:'chosen',efforts:['low','high'],defaultEffort:'low'}],currentModel:'chosen',currentEffort:'low',cache:{identity:'account'},capabilities:{models:true,effort:true}}:url.endsWith('/create')?{status:'accepted',result:{id:'native'}}:url.includes('/detail')?{session:{id:'native',title:'Native',status:'idle'},messages:[]}:{sessions:[]});
  await ui.view.create();await new Promise(setImmediate);const dialog=ui.document.querySelector('dialog');
  assert.equal(dialog.querySelector('[aria-label="模型"]').value,'chosen');assert.equal(dialog.querySelector('[aria-label="思考强度"]').value,'low');assert.equal(ui.calls.some(call=>call.body),false);
  dialog.querySelector('[aria-label="思考强度"]').value='high';dialog.querySelector('[aria-label="思考强度"]').onchange();dialog.querySelector('textarea').value='FIRST';
  await dialog.querySelector('form').onsubmit({preventDefault(){}});
  const sent=ui.calls.find(call=>call.url.endsWith('/create')).body;assert.equal(sent.model,'chosen');assert.equal(sent.effort,'high');assert.equal(sent.modelCatalogIdentity,'account');assert.equal(sent.environment,'scope');
  if(provider!=='deepseek')assert.equal(sent.firstMessage,'FIRST');
 }
});

test('creation picker never chooses the first model and shares cache refresh with isolated scopes',async()=>{
 const ui=fixture(),Picker=ui.run('window.BridgeCreationModelPicker'),root=ui.document.createElement('section'),cache=new Map();let finished,requests=[];
 const known={models:[{id:'first-expensive',efforts:['high']},{id:'flash',efforts:['low'],isDefault:true,defaultEffort:'low'}],currentModel:'flash',currentEffort:'low',cache:{identity:'account-a'}};ui.run('window.BridgeModelCatalogCache').bind(cache,'a',[],known);
 const picker=new Picker({root,cache});const read=refresh=>{requests.push(refresh);return new Promise(resolve=>finished=resolve);};const pending=picker.load('a',read);
 assert.equal(picker.model.value,'flash');assert.equal(picker.valid(),true);finished(known);await pending;
 await picker.load('a',read);assert.equal(requests.length,1,'Opening the same scope reuses its loaded catalog');
 const refresh=picker.refresh.onclick();assert.equal(requests[1],true);finished({...known,cache:{identity:'account-a',updatedAt:42}});await refresh;assert.equal(ui.run('window.BridgeModelCatalogCache').forScope(cache,'a').cache.updatedAt,42);
 const other=picker.load('b',async()=>({models:known.models.map(row=>({...row,isDefault:false})),cache:{identity:'account-b'}}));await other;assert.equal(picker.model.value,'');assert.equal(picker.valid(),false);
 let stale;const old=picker.load('c',()=>new Promise(resolve=>stale=resolve));await picker.load('d',async()=>({...known,cache:{identity:'account-d'}}));stale({...known,cache:{identity:'old'}});await old;assert.equal(picker.catalog.cache.identity,'account-d');
 picker.dispose();
});

test('cached creation choices survive the initial background catalog read for every desktop provider',async()=>{
 for(const provider of ['claude','deepseek','zcode']){
  const ui=fixture();ui.view.provider=provider;ui.view.generation=1;ui.view.open=async()=>{};
  const environment={key:'scope',surface:provider==='claude'?'code':'desktop',host:'local',createRequiresMessage:provider!=='deepseek',createTitle:provider!=='zcode'};
  const catalog={models:[{id:'native-default',efforts:['high']},{id:'chosen',efforts:['low','high'],defaultEffort:'high'}],currentModel:'native-default',currentEffort:'high',cache:{identity:'account'},capabilities:{models:true,effort:true}};
  let pending=false,finish;
  ui.setHandler(async(url,body)=>url.endsWith('/projects')?{createModels:true,projects:[{key:'p',environment:'scope',surface:environment.surface,host:'local',cwd:'/p',name:'Project'}],environments:[environment],browseDirectories:false}:url.includes('/create-models?')?(pending?new Promise(resolve=>finish=resolve):catalog):url.endsWith('/create')?{status:'accepted',result:{id:'native'}}:{sessions:[]});
  await ui.view.create();await new Promise(setImmediate);ui.document.querySelector('dialog').close();
  pending=true;await ui.view.create();
  const dialog=ui.document.querySelectorAll('dialog').find(node=>node.open),model=dialog.querySelector('[aria-label="模型"]'),effort=dialog.querySelector('[aria-label="思考强度"]');
  model.value='chosen';model.onchange();effort.value='low';effort.onchange();
  finish(catalog);await new Promise(setImmediate);
  assert.equal(model.value,'chosen',provider+' keeps the explicit choice made while verifying cached models');assert.equal(effort.value,'low');
  dialog.querySelector('textarea').value='FIRST';await dialog.querySelector('form').onsubmit({preventDefault(){}});
  const body=ui.calls.find(call=>call.url.endsWith('/create')).body;assert.equal(body.model,'chosen');assert.equal(body.effort,'low');
 }
});

test('creation captures its model before directory registration and account changes still invalidate edited choices',async()=>{
 const ui=fixture();ui.view.provider='deepseek';ui.view.generation=1;ui.view.open=async()=>{};
 ui.run('window.BridgeProjectPicker={choose:async options=>options.onSelect({path:"/new"})}');
 const catalog={models:[{id:'original',efforts:['low'],defaultEffort:'low',isDefault:true},{id:'later',efforts:['high']}],currentModel:'original',currentEffort:'low',cache:{identity:'account'},capabilities:{models:true,effort:true}};
 let pending=false,modelsReady,registered;
 ui.setHandler(async(url,body)=>url.endsWith('/projects')?{createModels:true,projects:[],environments:[{key:'scope',surface:'desktop',host:'local'}]}:url.includes('/create-models?')?(pending?new Promise(resolve=>modelsReady=resolve):catalog):url.endsWith('/register')?new Promise(resolve=>registered=resolve):url.endsWith('/create')?{status:'ready',result:{id:'native'}}:{sessions:[]});
 const open=async()=>{await ui.view.create();const dialog=ui.document.querySelectorAll('dialog').find(node=>node.open);await dialog.querySelector('.project-picker-browse').onclick();await new Promise(setImmediate);return dialog;};
 (await open()).close();pending=true;const dialog=await open();
 const sending=dialog.querySelector('form').onsubmit({preventDefault(){}});
 modelsReady({...catalog,currentModel:'later',currentEffort:'high'});await new Promise(setImmediate);
 registered({project:{key:'registered',cwd:'/new'}});await sending;
 const body=ui.calls.find(call=>call.url.endsWith('/create')).body;assert.equal(body.model,'original');assert.equal(body.effort,'low');

 const Picker=ui.run('window.BridgeCreationModelPicker'),cache=new Map();ui.run('window.BridgeModelCatalogCache').bind(cache,'scope',[],catalog);
 const picker=new Picker({root:ui.document.createElement('section'),cache});let finish;const reading=picker.load('scope',()=>new Promise(resolve=>finish=resolve));
 picker.model.value='later';picker.model.onchange();picker.effort.value='high';picker.effort.onchange();
 finish({...catalog,cache:{identity:'different-account'}});await reading;
 assert.equal(picker.model.value,'');assert.equal(picker.valid(),false,'An edited choice cannot survive a changed account identity');picker.dispose();
});

test('refreshing creation catalog identity never rotates an uncertain creation request',async()=>{
 const ui=fixture();ui.view.provider='zcode';ui.view.generation=1;
 ui.setHandler(async()=>({status:'unknown',message:'receipt unknown'}));
 const body={projectKey:'p',firstMessage:'FIRST',model:'chosen',effort:'low',modelCatalogIdentity:'old',environment:'old-scope'};
 await ui.view.mutate('create',body,null);
 await ui.view.mutate('create',{...body,modelCatalogIdentity:'refreshed',environment:'refreshed-scope'},null);
 const sent=ui.calls.filter(call=>call.body);assert.equal(sent.length,2);assert.equal(sent[0].body.id,sent[1].body.id);
 ui.setHandler(async()=>({status:'blocked',message:'no input sent'}));await ui.view.mutate('create',{...body,firstMessage:'BLOCKED'},null);
 const blocked=ui.calls.filter(call=>call.body).at(-1).body.id;await ui.view.mutate('create',{...body,firstMessage:'BLOCKED',modelCatalogIdentity:'fixed'},null);
 assert.notEqual(ui.calls.filter(call=>call.body).at(-1).body.id,blocked,'A known preflight block can be explicitly retried');
});

test('automatic send policy respects native queue availability',async()=>{
 const ui=await opened(),v=ui.view;v.provider='zcode';v.applyDetail({connected:true,session:{id:'a',status:'active',capabilities:{send:true,queue:true,steer:true,sendModes:{send:false,queue:true,steer:true}}},messages:[]});
 v.mode.value='send';v.updateComposer();assert.equal(v.mode.value,'queue');assert.equal(v.send.disabled,false);
 v.mode.value='queue';v.updateComposer();assert.equal(v.send.disabled,false);
 v.mode.value='steer';v.updateComposer();assert.equal(v.send.disabled,false);
 v.applyDetail({connected:true,session:{id:'a',status:'idle',capabilities:{send:true,queue:true,steer:false,sendModes:{send:true,queue:true,steer:false}}},messages:[]});
 assert.equal(v.mode.value,'send');assert.equal(v.send.disabled,false);
});

test('shared context dialog shows native cache and threshold and blocks stale compaction',async()=>{
 const ui=await opened(),view=ui.view;
 view.applyDetail({session:{id:'a',status:'idle',contextUsage:{usedTokens:250,contextWindow:1000,autoCompactThresholdTokens:800,cache:{cacheReadTokens:100}},capabilities:{compact:true,tasks:true}},messages:[]});
 view.contextDetails();const dialog=view.contextUsage.dialog;
 assert.match(dialog.textContent,/自动压缩阈值.*800/);assert.match(dialog.textContent,/缓存读取 Token.*100/);
 const compact=dialog.querySelectorAll('button').find(n=>n.textContent==='压缩上下文');assert.equal(compact.disabled,false);
 view.staleDetail();assert.equal(compact.disabled,true);assert.equal(view.workbench.current.capabilities.tasks,true);
});

test('desktop native edit uses the shared message action dialog and one receipt id',async()=>{
 const ui=await opened(),view=ui.view;let openedAction;
 view.messageActions={open:(...args)=>openedAction=args};
 view.applyDetail({session:{id:'a',status:'idle',capabilities:{editMessage:true}},messages:[{id:'u',role:'user',text:'before',editMessageId:'native-token'}]});
 const button=view.messages.querySelectorAll('button').find(n=>n.textContent==='编辑并重新发送');assert.ok(button);button.click();
 assert.equal(openedAction[0],'edit');assert.equal(await openedAction[2].fullText(),'before');
 ui.setHandler(async(url,body)=>body?{status:'accepted',result:{id:'a'}}:{session:{id:'a'},messages:[]});
 const result=await openedAction[3].request('',{id:'same-attempt',text:'after'});assert.equal(result.status,'accepted');
 assert.ok(ui.calls.some(c=>c.url.includes('/edit-message')&&c.body?.id==='same-attempt'&&c.body.text==='after'));
});

test('saved ZCode history can be read without a live client and never enables the composer',async()=>{
 const ui=fixture(),view=ui.view;
 const row={id:'sess_1',title:'Saved',readOnly:true,connectionState:'detached',requests:[],capabilities:{send:false,resume:false,stop:false}};
 ui.setHandler(async url=>url.includes('/detail')?{readOnly:true,session:row,messages:[{id:'m',role:'assistant',text:'Saved reply'}],capabilities:row.capabilities}:url.includes('/list')?{readOnly:true,connected:false,sessions:[row]}:{});
 view.setClientStates([{id:'zcode',enabled:true,connected:false,historyAvailable:true}]);
 await view.choose('zcode');assert.equal(view.rows.length,1);assert.match(view.listStatus.textContent,/只读/);
 await view.open(row.id);assert.match(view.status.textContent,/只读/);assert.equal(view.canMutate(),false);assert.equal(view.canResume(),false);
 // An older cached row must stay read-only if a new live route is reported meanwhile.
 view.clientStates=[{id:'zcode',enabled:true,connected:true}];assert.equal(view.canMutate(),false);
 assert.equal(ui.calls.some(call=>call.body),false);
});


test('a verified per-session connection enables only text without granting app-wide control',async()=>{
 const ui=fixture(),view=ui.view;
 let bound=false;
 const client={id:'zcode',enabled:true,connected:false,historyAvailable:true};
 const row=()=>({id:'sess_A',title:'A',cwd:'/project',controlScope:'session',readOnly:!bound,
   connectionState:bound?'ready':'detached',requests:[],capabilities:{send:bound,resume:!bound,stop:false,
   skills:false,models:false,attachments:false,projectReferences:false,queue:false,steer:false,notifications:false}});
 ui.setHandler(async(url,body)=>{
   if(url.includes('/resume')){bound=true;return {status:'ready'};}
   if(url.includes('/detail'))return {session:row(),readOnly:!bound,messages:[],capabilities:row().capabilities};
   if(url.includes('/list'))return {sessionAccess:true,readOnly:true,connected:false,sessions:[row()]};
   return {};
 });
 view.setClientStates([client]);await view.choose('zcode');await view.open('sess_A');
 assert.equal(view.canResume(),true);view.setClientStates([client]);assert.equal(view.canResume(),true);
 await view.mutate('resume',{});assert.equal(bound,true);
 await view.detail(view.generation);assert.equal(view.canMutate(),true);
 assert.equal(view.attachButton.hidden,true);assert.equal(view.attachments.canUpload(),false);assert.equal(view.attachments.canReference(),false);assert.equal(view.modelButton.disabled,true);assert.equal(view.workMode.hidden,true);
 assert.equal(view.stop.hidden,true);assert.equal(view.skillsButton.hidden,true);assert.equal(view.canResume(),false);
 assert.equal(ui.calls.filter(c=>c.body).length,1);
});

test('only selecting a ZCode row continues its verified private session',async()=>{
 const ui=fixture(),view=ui.view,actions=[];
 view.provider='zcode';view.clientStates=[{id:'zcode',enabled:true,pipeReady:true}];
 view.detail=async()=>{};view.loadNotificationPolicy=()=>{};view.canResume=()=>true;
 view.mutate=async action=>actions.push(action);
 await view.open('cold');
 assert.deepEqual(actions,[]);
 await view.open('cold',{continueSelected:true});
 assert.deepEqual(actions,['resume']);
 view.clientStates[0].pipeReady=false;
 await view.open('cold',{continueSelected:true});
 assert.deepEqual(actions,['resume']);
});

async function progressiveChat(options){
 const ui=fixture(options),view=ui.view,session={id:'sess-cold',title:'Cold',status:'idle',cwd:'/p',requests:[]};
 const detail=ready=>({session:{...session,controlScope:'session',connectionState:ready?'ready':'detached'},
   capabilities:{resume:!ready,progressiveResume:!ready,send:ready},messages:[{id:'m',role:'assistant',text:'History'}]});
 const client={id:'zcode',label:'ZCode',enabled:true,connected:false,historyAvailable:true,pipeReady:true,setupStatus:'session-access'};
 let read=detail(false),respond=async()=>({status:'pending',progress:0});
 ui.setHandler(async(url,body,signal)=>{
   if(body){assert.match(url,/\/resume$/);return respond(body,signal);}
   if(url.includes('/detail'))return read;return {sessions:[session]};
 });
 view.bindDraftGateway('gateway');view.setClientStates([client]);await view.choose('zcode');await view.open(session.id);ui.calls.length=0;
 return {...ui,answer:fn=>respond=fn,ready:()=>read=detail(true),session,detail};
}

test('weak route retries only the same resume receipt and a 17s response fits the second transport budget',async()=>{
 const ui=await progressiveChat(),view=ui.view,requests=[];
 const timer=ui.run("globalThis.timeouts=new Map();let serial=0;globalThis.now=0;Date.now=()=>now;setTimeout=(fn,ms)=>{timeouts.set(++serial,{fn,ms});return serial;};clearTimeout=id=>timeouts.delete(id);timeouts");
 view.input.value='草稿';view.draftTouched=true;view.messages.scrollHeight=1000;view.messages.scrollTop=31;view.rememberReading();const listNode=view.messageNodes.get('m').node;
 let settle;ui.answer((body,signal)=>{requests.push(body);return new Promise((resolve,reject)=>{settle=resolve;signal.addEventListener('abort',()=>reject(signal.reason));});});
 const first=view.continueChat();assert.equal(requests.length,1);assert.equal(!!view.busy,false);
 const timeLimit=[...timer.values()].find(t=>t.ms===12000);assert.ok(timeLimit);
 ui.run('now=12000');timeLimit.fn();await first;assert.equal(view.resumeAttempt.timeout,24000);assert.match(view.notice.textContent,/等待网络/);
 const before=ui.calls.length;await view.refresh();assert.equal(ui.calls.length,before);
 const poll=timer.get(view.resumeAttempt.timer);assert.equal(poll.ms,1000);const later=poll.fn();
 assert.equal(requests.length,2);assert.equal(requests[0].id,requests[1].id);assert.equal(requests[0].progressive,true);
 assert.ok([...timer.values()].some(t=>t.ms===24000));assert.equal(view.send.disabled,true);
 ui.run('now=29000');ui.ready();settle({status:'ready'});await later;
 assert.equal(view.canMutate(),true);assert.equal(view.savedResumeId(),null);
 assert.equal(ui.calls.filter(c=>/\/detail/.test(c.url)).length,1);assert.equal(ui.calls.filter(c=>/\/list/.test(c.url)).length,0);
 assert.ok(requests.every(b=>b.id===requests[0].id&&b.sessionId===ui.session.id));
 assert.equal(view.input.value,'草稿');assert.equal(view.messages.scrollTop,31);assert.equal(view.messageNodes.get('m').node,listNode);
});

test('hidden or switched chats stop polling and reload resumes the stored UUID only for the same gateway',async()=>{
 const ui=await progressiveChat();const view=ui.view;
 await view.continueChat();const id=view.resumeAttempt.id;assert.equal(view.savedResumeId(),id);
 ui.document.hidden=true;ui.document.dispatchEvent({type:'visibilitychange'});await view.checkResume(view.resumeAttempt);
 assert.equal(ui.calls.filter(c=>c.body).length,1);
 ui.document.hidden=false;ui.document.dispatchEvent({type:'visibilitychange'});await new Promise(setImmediate);
 assert.equal(ui.calls.filter(c=>c.body).length,2);
 view.backToList();await view.checkResume(view.resumeAttempt);assert.equal(view.resumeAttempt,null);
 const returned=await progressiveChat({sessionStore:ui.sessionStore});await new Promise(setImmediate);
 assert.equal(returned.view.resumeAttempt.id,id);
 returned.view.bindDraftGateway('other');assert.equal(returned.view.resumeAttempt,null);assert.equal(returned.view.savedResumeId(),null);
});

test('channel handoff rechecks one pending UUID and a blocked response permits exactly one explicit new attempt',async()=>{
 const ui=await progressiveChat(),view=ui.view;let settle;
 const timers=ui.run("globalThis.timers=new Map();let serial=0;setTimeout=(fn,ms)=>{timers.set(++serial,{fn,ms});return serial;};clearTimeout=id=>timers.delete(id);timers");
 ui.answer((_body,signal)=>new Promise((resolve,reject)=>{settle=resolve;signal.addEventListener('abort',()=>reject(signal.reason));}));
 const run=view.continueChat(),id=view.resumeAttempt.id;ui.run("window.dispatchEvent({type:'bridge-channel-changed'})");await run;
 const wake=timers.get(view.resumeAttempt.timer);assert.equal(wake.ms,0);const pending=wake.fn();assert.equal(ui.calls.filter(c=>c.body).at(-1).body.id,id);
 settle({status:'blocked',message:'原会话已关闭'});await pending;await new Promise(setImmediate);
 assert.match(view.error.textContent,/原会话已关闭/);assert.equal(view.savedResumeId(),null);assert.equal(view.resumeAttempt,null);
 const writes=ui.calls.filter(c=>c.body).length;await view.refresh();assert.equal(ui.calls.filter(c=>c.body).length,writes);
 ui.answer(async()=>({status:'pending'}));await view.resumeButton.click();assert.notEqual(view.resumeAttempt.id,id);
});


test('ready connection methods hide retry without placing a chooser above the chat list',()=>{
 const ui=fixture(),view=ui.view;view.provider='zcode';
 const client={id:'zcode',enabled:true,setupStatus:'session-access',connected:false,historyAvailable:true,retryable:true,connectionMode:'official-remote',connectionModes:[{id:'private-pipe',label:'本机连接',configured:true},{id:'official-remote',label:'官方远控',configured:true}]};
 view.setClientStates([client]);
 assert.equal(view.connectionRetry.hidden,true);assert.equal(view.connectionMode,undefined);assert.equal(view.listStatus.textContent,'');
 assert.equal(view.list.querySelectorAll('button').some(button=>button.textContent==='连接方式'),false);
});

test('connection method changes invalidate old write state without clearing drafts or reading position',async()=>{
 const ui=fixture(),view=ui.view;view.provider='zcode';view.sid='fixture';
 const client={id:'zcode',enabled:true,connected:true,historyAvailable:true,connectionMode:'private-pipe'};view.setClientStates([client]);
 view.state={id:'fixture',title:'Fixture',status:'idle',capabilities:{send:true}};view.freshDetail=true;view.input.value='Keep draft';view.messages.scrollTop=123;
 const attachments=view.attachments,messageList=view.messageList;let reads=0;view.refresh=async()=>{reads++;};
 await view.setClientStates([{...client,connectionMode:'official-remote'}]);
 assert.equal(view.freshDetail,false);assert.equal(view.canMutate(),false);assert.equal(reads,1);assert.equal(view.input.value,'Keep draft');assert.equal(view.messages.scrollTop,123);assert.equal(view.attachments,attachments);assert.equal(view.messageList,messageList);
 await view.setClientStates([{...client,connectionMode:'official-remote'}]);assert.equal(reads,1);
});


test('saved composer presentation keeps native controls stable without granting live permissions',async()=>{
 const ui=fixture(),view=ui.view,sid='sess_presentation',presentation={models:true,effort:true,skills:true,permissions:true,attachments:true,plan:true,goal:true};
 view.provider='zcode';view.setClientStates([{id:'zcode',enabled:true,connected:true,historyAvailable:true}]);
 const session={id:sid,title:'Native',cwd:'/project',model:'account:zai-start-plan/GLM-5.3-Flash$low',modelDisplayName:'GLM-5.3-Flash',effort:'low',composerCapabilities:presentation};
 view.rows=[session];view.prepareChat(sid);
 const controls=[view.modelButton,view.skillsButton,view.permissionsButton,view.attachButton,view.workMode];
 assert.deepEqual(controls.map(button=>button.hidden),[false,false,false,false,false]);assert.equal(view.modelName.textContent,'GLM-5.3-Flash');
 const saved={session:{...session,status:'unknown',connectionState:'detached',readOnly:true},readOnly:true,capabilities:{send:false,resume:true,stop:false,plan:false,goal:false},messages:[{id:'saved',role:'assistant',text:'Saved reply'}]};
 view.workModes.selection='plan';view.applyDetail(saved);
 assert.equal(view.workMode.value,'plan');assert.equal(view.workMode.disabled,true);assert.equal(view.send.disabled,true);
 assert.equal(view.modelButton.disabled,true);assert.equal(view.skillsButton.disabled,true);assert.equal(view.permissionsButton.disabled,true);assert.equal(view.attachments.locked,true);
 const calls=ui.calls.length;await view.models();await view.skills();await view.permissions();assert.equal(ui.calls.length,calls);
 view.input.value='Keep draft';view.attachments.rows.push({id:'saved-upload',status:'ready',name:'file.png'});view.messages.scrollTop=42;view.reading={following:false,top:42};const before=view.messageList.children[0];
 const live={session:{...session,status:'idle',connectionState:'ready',permissionMode:'build',permissionLabel:'变更前确认'},capabilities:{send:true,models:true,skills:true,permissions:true,attachments:true,plan:true,goal:true},messages:saved.messages};
 view.applyDetail(live);assert.deepEqual(controls.map(button=>button.hidden),[false,false,false,false,false]);assert.equal(view.workMode.value,'plan');assert.equal(view.workMode.disabled,false);assert.equal(view.skillsButton.disabled,false);assert.equal(view.modelButton.disabled,false);
 assert.equal(view.input.value,'Keep draft');assert.equal(view.attachments.rows[0].id,'saved-upload');assert.equal(view.messages.scrollTop,42);assert.equal(view.messageList.children[0],before);
 view.applyDetail(saved);assert.deepEqual(controls.map(button=>button.hidden),[false,false,false,false,false]);assert.equal(view.send.disabled,true);assert.equal(view.workMode.value,'plan');assert.equal(view.input.value,'Keep draft');assert.equal(view.attachments.rows[0].id,'saved-upload');
});


test('presentation-only work mode does not add unsupported commands to a plain message',async()=>{
 const ui=fixture(),view=ui.view;view.provider='zcode';view.sid='a';view.setClientStates([{id:'zcode',connected:true,enabled:true}]);view.prepareChat('a');
 view.applyDetail({session:{id:'a',title:'Limited',status:'idle',connectionState:'ready',composerCapabilities:{plan:true,goal:true}},capabilities:{send:true,plan:false,goal:false},messages:[]});
 assert.equal(view.workMode.hidden,false);assert.equal(view.workMode.disabled,true);view.input.value='plain message';ui.setHandler(async()=>({status:'unknown'}));await view.submit();
 const sent=ui.calls.find(call=>call.body);assert.ok(sent);assert.equal(sent.body.workMode,undefined);
});


test('normal connection summaries disappear without swallowing later loading and read failures',async()=>{
 const ui=fixture(),view=ui.view;view.provider='zcode';const client={id:'zcode',enabled:true,connected:false,retryable:true,historyAvailable:true,connectionMode:'official-remote',connectionModes:[{id:'official-remote',label:'官方远控',configured:true}]};
 view.setClientStates([client]);assert.match(view.listStatus.textContent,/尚未连接.*官方远控/);view.refresh=async()=>{};await view.setClientStates([{...client,connected:true}]);assert.equal(view.listStatus.textContent,'');
 view.listStatusKind='loading';view.listStatus.textContent='正在读取聊天…';view.renderConnectionStatus();assert.equal(view.listStatus.textContent,'正在读取聊天…');
 view.listStatusKind='error';view.listStatus.textContent='列表读取失败';view.renderConnectionStatus();assert.equal(view.listStatus.textContent,'列表读取失败');assert.equal(view.listStatus.hidden,false);
 await view.setClientStates([client]);assert.match(view.listStatus.textContent,/尚未连接/);assert.equal(view.connectionRetry.hidden,false);
});


test('chat and creation share refreshed catalogs without sharing selection or crossing instances',async()=>{
 const ui=await opened(),view=ui.view,shared=ui.run('window.BridgeModelCatalogCache'),Picker=ui.run('window.BridgeCreationModelPicker');
 view.provider='zcode';view.listCacheIdentity='gateway';view.listCacheHost='local';
 view.state={...view.state,model:'flash',effort:'low',modelCatalogIdentity:'identity-a',capabilities:{models:true,effort:true}};
 const namespace=['gateway','local','zcode'],cache=view.modelCatalogs,scope='new-project';
 const catalog=(id,time)=>({models:[{id,name:id,efforts:['low']}],cache:{identity:'identity-a',updatedAt:time},capabilities:{models:true,effort:true},currentModel:id,currentEffort:'low'});
 const first=catalog('flash',1);shared.bind(cache,scope,namespace,first);
 ui.setHandler(async()=>catalog('new-flash',2));await view.models();
 const fromChat=shared.forScope(cache,scope);assert.equal(fromChat.models[0].id,'new-flash');assert.equal(fromChat.currentModel,undefined);assert.equal(fromChat.capabilities,undefined);
 view.modelDialog.close();
 const picker=new Picker({root:ui.document.createElement('section'),cache,namespace:()=>namespace});let finish;
 const pending=picker.load(scope,()=>new Promise(resolve=>finish=resolve));
 assert.equal(picker.rows[0].id,'new-flash','Creation immediately paints the refreshed chat catalog');assert.equal(picker.model.value,'','Another chat selection is never inherited');
 finish(catalog('new-flash',2));await pending;
 picker.read=async()=>catalog('latest',3);await picker.refresh.onclick();
 let resolveRead;ui.setHandler(()=>new Promise(resolve=>resolveRead=resolve));const opening=view.models();
 assert.ok(view.modelDialog.querySelector('input[value="latest"]')||view.modelDialog.textContent.includes('latest'),'Chat immediately paints creation refresh');
 resolveRead(catalog('latest',3));await opening;view.modelDialog.close();
 shared.write(cache,namespace,catalog('late-old',2));assert.equal(shared.forScope(cache,scope).models[0].id,'latest','Late older reads cannot overwrite a successful refresh');
 assert.equal(shared.read(cache,['other-gateway','local','zcode'],'identity-a'),null);
 assert.equal(shared.read(cache,['gateway','other-host','zcode'],'identity-a'),null);
 assert.equal(shared.read(cache,['gateway','local','zcode:other'],'identity-a'),null);
 assert.equal(shared.read(cache,namespace,'identity-b'),null);
 shared.bind(cache,'other-project',['gateway','local','claude'],first);shared.forget(cache,namespace);
 assert.equal(shared.forScope(cache,scope),null);assert.equal(shared.read(cache,namespace,'identity-a'),null);assert.ok(shared.forScope(cache,'other-project'));
 picker.dispose();view.clear();assert.equal(cache.size,0,'Logging out clears cached catalogs and aliases');
});


test('account directory refresh updates known Code and Cowork projections without mixing their models',()=>{
 const ui=fixture(),shared=ui.run('window.BridgeModelCatalogCache'),cache=new Map(),namespace=['gateway','local','claude'];
 const groups=(code,cowork)=>[{surface:'code',models:[{id:code}]},{surface:'cowork',models:[{id:cowork}]}];
 for(const surface of ['code','cowork'])shared.bind(cache,surface,namespace,{models:[{id:surface+'-old'}],groups:groups('code-old','cowork-old'),cache:{identity:surface+'-identity',directoryIdentity:'directory',surface,updatedAt:1}});
 shared.write(cache,namespace,{models:[{id:'code-new'},{id:'cowork-new'}],groups:groups('code-new','cowork-new'),cache:{identity:'directory',updatedAt:2}});
 assert.equal(shared.forScope(cache,'code').models[0].id,'code-new');assert.equal(shared.forScope(cache,'cowork').models[0].id,'cowork-new');
 assert.equal(shared.forScope(cache,'code').cache.identity,'code-identity');assert.equal(shared.forScope(cache,'cowork').cache.updatedAt,2);
 shared.write(cache,namespace,{models:[{id:'stale-code'}],groups:groups('stale-code','stale-cowork'),cache:{identity:'code-identity',directoryIdentity:'directory',surface:'code',updatedAt:1}});
 assert.equal(shared.forScope(cache,'code').models[0].id,'code-new');
 shared.write(cache,namespace,{models:[{id:'cowork-only'}],groups:[{surface:'cowork',models:[{id:'cowork-only'}]}],cache:{identity:'directory',updatedAt:3}});
 assert.equal(shared.forScope(cache,'code').models.length,0,'Missing Code group never borrows Cowork models');
 shared.write(cache,namespace,{models:[{id:'api-only'}],groups:[{surface:'api',models:[{id:'api-only'}]}],cache:{identity:'directory',updatedAt:4}});
 assert.equal(shared.forScope(cache,'code').models[0].id,'api-only','Explicit shared API catalog is available to each surface');
 assert.equal(shared.forScope(cache,'cowork').models[0].id,'api-only');
 shared.write(cache,namespace,{models:[{id:'verified-code'}],groups:[],cache:{identity:'code-identity',directoryIdentity:'directory',surface:'code',updatedAt:5}});
 assert.equal(shared.forScope(cache,'code').models[0].id,'verified-code','Missing grouping metadata keeps only the verified projection');
 assert.equal(shared.forScope(cache,'cowork').models[0].id,'api-only','Ungrouped result cannot overwrite another surface');
 shared.write(cache,namespace,{models:[{id:'stale-code'}],groups:groups('stale-code','stale-cowork'),cache:{identity:'code-identity',directoryIdentity:'directory',surface:'code',updatedAt:1}});
 assert.equal(shared.forScope(cache,'code').models[0].id,'verified-code','An older grouped response cannot replace a newer ungrouped projection');
 assert.equal(shared.forScope(cache,'code').cache.updatedAt,5);

 shared.forget(cache,namespace);assert.equal(shared.forScope(cache,'code'),null);assert.equal(shared.forScope(cache,'cowork'),null);
});
