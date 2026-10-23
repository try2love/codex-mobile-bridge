'use strict';
const test=require('node:test'),assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const official={visible:true,loginType:'chatgpt',accountKey:'a'.repeat(64),limits:[],resetCredits:null,canReset:false,updatedAt:1900000000};

async function fixture(read,kind='chatgpt',catalog=null,gatewayLanguage,accountSnapshot=null){
  const nodes=new Map(),timers=new Map(),requests=[],intervals=[],events={},responses=[];let timerId=0,delayNextSkill=null,failNextSkill=false;
  const node=(tag='div')=>({tagName:tag.toUpperCase(),getBoundingClientRect(){return {width:0};},value:'',checked:false,disabled:false,hidden:false,open:false,textContent:'',dataset:{},options:[],children:[],style:{setProperty(k,v){this[k]=v;}},scrollTop:0,parentElement:{},
    classList:{add(){},remove(){},toggle(){},contains(){return false;}},addEventListener(){},setAttribute(k,v){this[k]=v;},closest(selector){return selector==='dialog'?nodes.get('accounts-dialog'):null;},
    replaceChildren(...children){for(const child of this.children)child.parentElement=null;this.children=[];this.append(...children);},append(...children){for(const child of children){child.remove?.();child.parentElement=this;this.children.push(child);}},insertBefore(child,before){if(child===before)return child;child.remove?.();const index=before?this.children.indexOf(before):-1;this.children.splice(index<0?this.children.length:index,0,child);child.parentElement=this;return child;},remove(){const parent=this.parentElement;if(parent?.children)parent.children=parent.children.filter(child=>child!==this);this.parentElement=null;},after(child){const parent=this.parentElement;if(parent?.children){parent.children.splice(parent.children.indexOf(this)+1,0,child);child.parentElement=parent;}},querySelectorAll(){return [];},querySelector(){return null;},
    showModal(){this.open=true;},close(){this.open=false;}});
  const html=fs.readFileSync(path.join(__dirname,'../web/index.html'),'utf8');
  for(const match of html.matchAll(/<([a-z]+)\b([^>]*\bid="([^"]+)"[^>]*)>/g)){
    const n=node(match[1]);n.hidden=/\bhidden\b/.test(match[2]);nodes.set(match[3],n);
  }
  const storage=()=>({getItem(){return null;},setItem(){},clear(){}});
  const response=(data,status=200)=>({ok:status===200,status,json:async()=>data});
  const context=vm.createContext({ClientNavigation:class{constructor(){this.provider="codex";}async refresh(){}async openChatLink(hash){assert.equal(hash,'','This fixture enters without a chat deep link');return false;}},DesktopSessionsView:class{bindListCache(){}previewCachedList(){return false;}clear(){}},Workbench:class{open(){}reset(){}setThumbnails(){}relabel(){}},Event:class{constructor(type){this.type=type;}},navigator:{userAgent:'test'},Date,Option:function(text,value){return {...node('option'),textContent:text,value};},document:{addEventListener(){},dispatchEvent(){},documentElement:{},getElementById:id=>nodes.get(id),querySelector:()=>({classList:{toggle(){}}}),querySelectorAll:()=>[],createElement:node},
    window:{addEventListener:(name,fn)=>events[name]=fn},localStorage:storage(),sessionStorage:storage(),
    location:{hash:'',pathname:'/',search:''},history:{replaceState(){}},
    setTimeout(fn,delay){timers.set(++timerId,{fn,delay});return timerId;},clearTimeout(id){timers.delete(id);},setInterval(fn,delay){intervals.push({fn,delay});},
    fetch:async(url,options={})=>{
      requests.push(url);
      if(url==='/api/auth')return response({authenticated:true,csrf:'fixture',uiLanguage:gatewayLanguage});
      if(url.startsWith('/api/sessions?'))return response({sessions:[]});
      if(url==='/api/accounts')return response(accountSnapshot||{accounts:[],activeId:null,current:{kind,status:'ready',name:'Fixture'},switch:{phase:'idle'}});
      if(url.includes('/catalog?kind=skills')){
        if(failNextSkill){failNextSkill=false;return response({error:'Skill fixture error'},500);}
        if(delayNextSkill){const wait=delayNextSkill;delayNextSkill=null;await wait.promise;}
        const params=new URL(url,'http://fixture').searchParams;const rows=catalog.skills||[];const query=(params.get('q')||'').toLowerCase();
        const filtered=rows.filter(row=>[row.name,row.displayName,row.description].join(' ').toLowerCase().includes(query));
        const offset=Number(params.get('offset')||0),limit=Number(params.get('limit')||200),ids=params.getAll('id');
        return response({skills:filtered.slice(offset,offset+limit),selectedSkills:rows.filter(row=>ids.includes(row.id)),errors:[],total:filtered.length,offset,limit,cache:{state:'fresh',updatedAt:1,bucketId:'fixture-bucket'}});
      }
      if(url.includes('/catalog?kind=models'))return response(catalog);
      if(url.includes('/catalog'))return response(catalog);
      if(url.includes('/settings')){responses.push(JSON.parse(options.body));return response({confirmed:true});}
      if(url.includes('/respond')){responses.push(JSON.parse(options.body));return response({});}
      throw Error('Unexpected request '+url);
    }});
  vm.runInContext(fs.readFileSync(path.join(__dirname,'../web/features/clients/client-navigation.js'),'utf8').match(/^function isCodexClient.*$/m)[0],context);
  for(const file of ['web/hosts/environment.js','web/shared/i18n.js','web/features/chat/list-sync.js','web/features/accounts/account.js','web/features/accounts/accounts.js','web/features/chat/modes.js','web/features/chat/attachments.js','web/features/chat/activity.js','web/features/chat/fast-mode.js','web/features/chat/message-actions.js','web/features/chat/permissions.js','web/shell/app.js'])vm.runInContext(fs.readFileSync(path.join(__dirname,'..',file),'utf8'),context);
  await new Promise(setImmediate);
  return {nodes,requests,timers,intervals,events,responses,run:code=>vm.runInContext(code,context),
    failNextSkill(){failNextSkill=true;},
    delayNextSkill(){let release;const promise=new Promise(resolve=>release=resolve);delayNextSkill={promise,release};return release;},
    async retry(){const entry=[...timers.entries()].find(([,t])=>t.delay<=5000);assert.ok(entry,'Account read should retry promptly without user input');timers.delete(entry[0]);await entry[1].fn();await new Promise(setImmediate);}};
}

test('WSL account URLs stay scoped and an explicit desktop choice restores desktop scope',async()=>{
 const ui=await fixture(r=>r(official));
 ui.run("currentId='fixture';currentHost='wsl:original';clientNavigation.provider='wsl:original';");
 assert.equal(ui.run("accountUrl('/api/accounts')"),'/api/accounts?entry=wsl%3Aoriginal');
 assert.equal(ui.run("accountUrl('/api/account?cached=1')"),'/api/account?cached=1&entry=wsl%3Aoriginal');
 ui.run("clientNavigation.provider='wsl:selected';");assert.equal(ui.run("accountUrl('/api/accounts')"),'/api/accounts?entry=wsl%3Aselected');
 ui.run("clientNavigation.provider='codex';");assert.equal(ui.run("accountUrl('/api/accounts')"),'/api/accounts');
});

test('official and API connections have no current-account fallback outside saved cards',async()=>{
 const desktopHtml=fs.readFileSync(path.join(__dirname,'../desktop/index.html'),'utf8');
 assert.doesNotMatch(desktopHtml,/id="account-(?:details|button|content)"/);
 for(const kind of ['chatgpt','api']){
  const ui=await fixture(r=>r(official),kind);
  assert.equal(ui.nodes.get('accounts-button').textContent,'账号管理');
  for(const id of ['account-status','account-dialog','account-details','account-button','account-content'])assert.equal(ui.nodes.has(id),false,id);
  assert.equal(ui.requests.filter(url=>url==='/api/account').length,0);
  ui.nodes.get('accounts-button').onclick();await new Promise(setImmediate);
  assert.equal(ui.nodes.get('accounts-dialog').open,true);
  assert.equal(ui.requests.some(url=>url==='/api/account'||url==='/api/account?cached=1'),false);
 }
});
test('saved official card retains its direct reset action without a second quota panel',async()=>{
 const account={id:'official',kind:'chatgpt',name:'Official',details:{usage:{status:'ready',checkedAt:Date.now()/1000,limits:[],resetCredits:{availableCount:1}}}};
 const ui=await fixture(r=>r(official),'chatgpt',null,null,{accounts:[account],activeId:account.id,current:{id:account.id,kind:'chatgpt',status:'ready',name:account.name},switch:{phase:'idle'},blockers:[]});
 ui.nodes.get('accounts-button').onclick();await new Promise(setImmediate);
 const all=node=>[node,...node.children.flatMap(all)];
 const card=all(ui.nodes.get('accounts-content')).find(node=>node.className==='account-bucket');
 assert.ok(card);assert.ok(all(card).some(node=>node.tagName==='BUTTON'&&node.textContent==='使用重置卡'));
 assert.equal(ui.nodes.has('account-details'),false);
 assert.equal(ui.requests.some(url=>url==='/api/account'||url==='/api/account?cached=1'),false);
});
test('opening, refreshing and logging out never read the old current-account endpoint',async()=>{
 const ui=await fixture(r=>r(official));
 ui.nodes.get('accounts-button').onclick();await new Promise(setImmediate);
 await ui.run('accountsPanel.refresh()');ui.run('showLogin()');
 assert.equal(ui.requests.some(url=>url==='/api/account'||url==='/api/account?cached=1'),false);
});
test('computer approval buttons send precisely the selected persistence and retain their original chat',async()=>{
 const ui=await fixture(r=>r(official));
 for(const [label,persist] of [['始终允许','always'],['允许此对话','session'],['拒绝',undefined]]){
  ui.run("currentId='11111111-1111-4111-8111-111111111111';currentHost='local';submittedRequestIds.clear();renderApprovals([{id:'computer-'+Math.random(),method:'mcpServer/elicitation/request',supported:true,params:{computerUse:{app:'Fixture app',persistModes:['session','always']}}}])");
  const all=n=>[n,...n.children.flatMap(all)],card=ui.nodes.get('approvals');
  assert.equal(all(card).some(n=>n.textContent==='本次允许'),false);
  const button=all(card).find(n=>n.textContent===label);assert.ok(button);
  ui.run("currentId='22222222-2222-4222-8222-222222222222'");await button.onclick();
  const reply=ui.responses.at(-1).response;assert.equal(reply.persist,persist);assert.equal(reply.action,persist?'accept':'decline');
  assert.match(ui.requests.at(-1),/11111111-1111-4111-8111-111111111111/);
 }
});

test('API chat picker shows upstream Gemini models and requires replacing the unavailable old model explicitly',async()=>{
 const catalog={models:[{id:'gemini-fixture',name:'gemini-fixture',efforts:[]}],skills:[],modelSource:'api',currentModel:'gpt-old',fastMode:{allowed:false}};
 const ui=await fixture(r=>r(official),'api',catalog);
 ui.run("currentId='11111111-1111-4111-8111-111111111111';state={model:'gpt-old',provider:'bridge_api',connected:true,effort:'high'}");
 await ui.nodes.get('model-button').onclick();
 const select=ui.nodes.get('model-select');assert.ok(select.children.some(o=>o.value==='gemini-fixture'));
 assert.equal(select.children.some(o=>o.value.startsWith('gpt')),false);assert.equal(select.value,'');
 assert.match(ui.nodes.get('model-error').textContent,/当前聊天模型不在上游/);assert.equal(ui.nodes.get('model-save').disabled,true);
 assert.equal(ui.responses.length,0);
 const modelChoice=ui.nodes.get('model-options').children.find(row=>row.children[0].value==='gemini-fixture').children[0];
 modelChoice.checked=true;modelChoice.onchange();assert.equal(select.value,'gemini-fixture');
 const effortChoice=ui.nodes.get('effort-options').children.find(row=>row.children[0].value==='xhigh').children[0];
 effortChoice.checked=true;effortChoice.onchange();
 assert.equal(ui.nodes.get('model-save').disabled,false);assert.equal(ui.nodes.get('fast-mode-control').hidden,true);
 await ui.nodes.get('model-form').onsubmit({preventDefault(){}});
 assert.equal(ui.responses[0].model,'gemini-fixture');assert.equal(ui.responses[0].effort,'xhigh');
 assert.equal(ui.responses[0].fastMode,undefined);
});
test('API model discovery failure shows its error with manual input instead of stale GPT options',async()=>{
 const ui=await fixture(r=>r(official),'api',{models:[],skills:[],modelSource:'api',modelError:'上游拒绝访问，请检查 API Key 和模型列表权限',fastMode:{allowed:false}});
 ui.run("currentId='11111111-1111-4111-8111-111111111111';state={model:'gpt-old',provider:'openai',connected:true}");
 await ui.nodes.get('model-button').onclick();
 assert.match(ui.nodes.get('model-error').textContent,/上游拒绝/);assert.equal(ui.nodes.get('custom-model-label').hidden,false);
 assert.equal(ui.nodes.get('model-select').children.some(o=>o.value.startsWith('gpt')),false);
 assert.equal(ui.responses.length,0);
});

test('a native aggregate route exposes Fast and submits the full routed model ID',async()=>{
 const route='route/gpt-fixture',other='route/other-fixture',catalog={models:[{id:route,name:'GPT fixture',efforts:['high'],fastTier:'priority'},{id:other,name:'Other fixture',efforts:['low']}],modelSource:'codex',fastMode:{allowed:true,provider:'custom'}};
 const ui=await fixture(r=>r(official),'api',catalog);
 ui.run("currentId='11111111-1111-4111-8111-111111111111';state={model:'route/gpt-fixture',provider:'custom',connected:true,effort:'high',serviceTier:'default'}");
 await ui.nodes.get('model-button').onclick();assert.equal(ui.nodes.get('fast-mode-control').hidden,false);assert.equal(ui.nodes.get('fast-mode-enabled').checked,false);
 const choices=ui.nodes.get('model-options').children;
 const choose=id=>{const input=choices.find(row=>row.children[0].value===id).children[0];input.checked=true;input.onchange();};
 choose(other);assert.equal(ui.nodes.get('model-select').value,other);assert.equal(ui.nodes.get('fast-mode-control').hidden,true);
 choose(route);assert.equal(ui.nodes.get('model-select').value,route);assert.equal(ui.nodes.get('fast-mode-control').hidden,false);
 assert.equal(ui.nodes.get('effort-options').children[0].children[0].value,'high');
 ui.nodes.get('fast-mode-enabled').checked=true;ui.nodes.get('fast-mode-enabled').onchange();
 await ui.nodes.get('model-form').onsubmit({preventDefault(){}});
 assert.deepEqual(ui.responses[0],{model:route,effort:'high',fastMode:true});
});
test('selected skill pills render before the catalog response arrives',async()=>{
  const ui=await fixture(r=>r(official));
  ui.run("currentId='11111111-1111-4111-8111-111111111111';currentHost='local';catalogData={models:null,skills:null};selectedSkills=new Set(['fixture-skill']);renderSkillPills()");
  assert.equal(ui.nodes.get('skill-count').textContent,'(1)');
  assert.equal(ui.nodes.get('skill-pills').children[0].textContent,'已选 Skill ×');
});
test('cached async model catalog still renders the picker instead of returning raw rows',async()=>{
  const catalog={models:[{id:'fixture-model',name:'Fixture',efforts:['high']}],skills:[],modelSource:'codex',currentModel:'fixture-model',fastMode:{allowed:false}};
  const ui=await fixture(r=>r(official),'api',catalog);
  ui.run("currentId='11111111-1111-4111-8111-111111111111';currentHost='local';state={connected:true,model:'fixture-model'}");
  ui.run("catalogData.models=[{id:'fixture-model',name:'Fixture',efforts:['high']}];catalogResults.models=null");
  await ui.nodes.get('model-button').onclick();
  assert.equal(ui.nodes.get('model-error').textContent,'');
  assert.ok(ui.nodes.get('model-select').children.some(o=>o.value==='fixture-model'));
  assert.equal(ui.nodes.get('model-save').disabled,false);
  await ui.nodes.get('model-button').onclick();
  assert.equal(ui.nodes.get('model-error').textContent,'');
  assert.ok(ui.nodes.get('model-select').children.some(o=>o.value==='fixture-model'));
});
test('model catalog accepts legacy array payloads and reports invalid payload types',async()=>{
  const catalog={data:[{id:'legacy-model',name:'Legacy',efforts:[]}],modelSource:'api',fastMode:{allowed:false}};
  const ui=await fixture(r=>r(official),'api',catalog);
  ui.run("currentId='11111111-1111-4111-8111-111111111111';currentHost='local';state={connected:true,model:'fixture-model'}");
  await ui.nodes.get('model-button').onclick();
  assert.ok(ui.nodes.get('model-select').children.some(o=>o.value==='legacy-model'));
  const bad=await fixture(r=>r(official),'api',{...catalog,models:'invalid'});
  bad.run("currentId='11111111-1111-4111-8111-111111111111';currentHost='local';state={connected:true,model:'fixture-model'}");
  await bad.nodes.get('model-button').onclick();
  assert.match(bad.nodes.get('model-error').textContent,/models: string/);
});
test('skill catalog preloads one page, renders full descriptions, and repeated opens do not refetch',async()=>{
  const rows=Array.from({length:5000},(_,i)=>({id:'skill-'+i,name:'skill-'+i,displayName:'Skill '+i,description:'Fixture skill '+i}));
  const ui=await fixture(r=>r(official),'api',{models:[],skills:rows,modelSource:'codex',fastMode:{allowed:false}});
  ui.run("currentId='11111111-1111-4111-8111-111111111111';currentHost='local';state={connected:true}");
  await ui.nodes.get('skills-button').onclick();
  const count=ui.requests.filter(url=>url.includes('/catalog?kind=skills')).length;
  assert.equal(count,1);
  assert.equal(ui.nodes.get('skill-list').children.length,200);
  const all=n=>[n,...n.children.flatMap(all)];assert.ok(all(ui.nodes.get('skill-list')).some(node=>node.textContent==='Skill 0'));
  assert.equal(all(ui.nodes.get('skill-list')).some(node=>node.textContent==='Skill 4000'),false);
  await ui.nodes.get('skills-button').onclick();
  assert.equal(ui.requests.filter(url=>url.includes('/catalog?kind=skills')).length,1);
});
test('skill search debounces to a new SQLite-backed page and refresh is explicit',async()=>{
  const rows=Array.from({length:3},(_,i)=>({id:'skill-'+i,name:'skill-'+i,displayName:'Skill '+i,description:'Fixture '+i}));
  const ui=await fixture(r=>r(official),'api',{models:[],skills:rows,modelSource:'codex',fastMode:{allowed:false}});
  ui.run("currentId='11111111-1111-4111-8111-111111111111';currentHost='local';state={connected:true}");
  await ui.nodes.get('skills-button').onclick();
  const search=ui.nodes.get('skill-search');search.value='fixture';search.oninput();
  const timer=[...ui.timers.entries()].find(([,t])=>t.delay===200);assert.ok(timer);
  ui.timers.delete(timer[0]);await timer[1].fn();
  const searched=ui.requests.filter(url=>url.includes('/catalog?kind=skills')).at(-1);
  assert.match(searched,/q=fixture/);assert.match(searched,/limit=200/);assert.match(searched,/offset=0/);
  await ui.nodes.get('skills-refresh').onclick();
  assert.match(ui.requests.filter(url=>url.includes('/catalog?kind=skills')).at(-1),/refresh=true/);
});
test('a stale skill append cannot overwrite a newer search result',async()=>{
  const rows=Array.from({length:500},(_,i)=>({id:'skill-'+i,name:i===0?'needle skill':'skill-'+i,displayName:i===0?'Needle Skill':'Skill '+i,description:'Fixture '+i}));
  const ui=await fixture(r=>r(official),'api',{models:[],skills:rows,modelSource:'codex',fastMode:{allowed:false}});
  ui.run("currentId='11111111-1111-4111-8111-111111111111';currentHost='local';state={connected:true}");
  await ui.nodes.get('skills-button').onclick();
  const releaseAppend=ui.delayNextSkill();
  const stale=ui.run('loadSkills({append:true})');
  ui.nodes.get('skill-search').value='needle';ui.nodes.get('skill-search').oninput();
  const timer=[...ui.timers.entries()].find(([,t])=>t.delay===200);assert.ok(timer);ui.timers.delete(timer[0]);
  await timer[1].fn();await new Promise(setImmediate);
  releaseAppend();await stale;await new Promise(setImmediate);
  assert.equal(ui.run('skillTotal'),1);
  assert.deepEqual(Array.from(ui.run('catalogData.skills.map(row=>row.id)')),['skill-0']);
  assert.equal(ui.run('skillQuery.offset'),1);
});
test('skill search and refresh reset the list scroll position',async()=>{
  const rows=Array.from({length:500},(_,i)=>({id:'skill-'+i,name:'skill-'+i,displayName:'Skill '+i,description:'Fixture '+i}));
  const ui=await fixture(r=>r(official),'api',{models:[],skills:rows,modelSource:'codex',fastMode:{allowed:false}});
  ui.run("currentId='11111111-1111-4111-8111-111111111111';currentHost='local';state={connected:true}");
  await ui.nodes.get('skills-button').onclick();
  ui.nodes.get('skill-list').scrollTop=20000;ui.run('renderSkills()');
  ui.nodes.get('skill-search').value='skill-499';ui.nodes.get('skill-search').oninput();
  const timer=[...ui.timers.entries()].find(([,t])=>t.delay===200);assert.ok(timer);ui.timers.delete(timer[0]);
  await timer[1].fn();
  assert.equal(ui.nodes.get('skill-list').scrollTop,0);
  assert.ok(ui.nodes.get('skill-list').children.length<=34);
  await ui.nodes.get('skills-refresh').onclick();
  assert.equal(ui.nodes.get('skill-list').scrollTop,0);
});
test('a missing selected skill is visibly invalid and blocks submission',async()=>{
  const rows=[{id:'available',name:'available',displayName:'Available',description:'Available'}];
  const ui=await fixture(r=>r(official),'api',{models:[],skills:rows,modelSource:'codex',fastMode:{allowed:false}});
  ui.run("currentId='11111111-1111-4111-8111-111111111111';currentHost='local';state={connected:true};selectedSkills=new Set(['available','missing']);attachments.ready=()=>true;attachments.ids=()=>[]");
  await ui.nodes.get('skills-button').onclick();
  assert.equal(ui.nodes.get('skill-count').textContent,'(2)');
  assert.ok([...ui.nodes.get('skill-pills').children].some(node=>node.textContent==='已失效 Skill ×'));
  ui.nodes.get('message').value='fixture';
  await ui.nodes.get('composer').onsubmit({preventDefault(){}});
  assert.equal(ui.nodes.get('send-error').textContent,'已选 Skill 已失效，请刷新列表后重新选择');
  assert.equal(ui.responses.length,0);
});
test('a skill selected during pagination is not invalidated by older hydration',async()=>{
  const rows=Array.from({length:500},(_,i)=>({id:'skill-'+i,name:'skill-'+i,displayName:'Skill '+i,description:'Fixture'}));
  const ui=await fixture(r=>r(official),'api',{models:[],skills:rows,modelSource:'codex',fastMode:{allowed:false}});
  ui.run("currentId='11111111-1111-4111-8111-111111111111';currentHost='local';state={connected:true}");
  await ui.nodes.get('skills-button').onclick();
  const release=ui.delayNextSkill(),pending=ui.run('loadSkills({append:true})');
  const all=n=>[n,...n.children.flatMap(all)];
  const checkbox=all(ui.nodes.get('skill-list')).find(n=>n.tagName==='INPUT');
  assert.ok(checkbox);checkbox.checked=true;checkbox.onchange();
  release();await pending;
  assert.deepEqual(Array.from(ui.run('[...selectedSkills]')),['skill-0']);
  assert.equal(ui.run('invalidSkills.size'),0);
});


test('Skill failure clears the loading state and refresh can recover',async()=>{
  const ui=await fixture(r=>r(official),'chatgpt',{skills:[{id:'skill',name:'sample',displayName:'Sample',description:'fixture'}]});
  ui.run("currentId='thread';currentHost='local'");
  ui.failNextSkill();
  await ui.run('openSkills()');
  assert.equal(ui.nodes.get('skill-list').textContent,'Skill 加载失败，请点击刷新重试');
  assert.equal(ui.nodes.get('skills-error').textContent,'Skill fixture error');
  await ui.run('openSkills(true)');
  assert.equal(ui.run('skillPages.length'),1);
  assert.equal(ui.nodes.get('skills-error').textContent,'');
});


test('Skill pagination uses actual scroll geometry and keeps complete descriptions',async()=>{
  const description='Full multiline Skill description. '.repeat(30);
  const rows=Array.from({length:220},(_,i)=>({id:'skill-'+i,name:'skill-'+i,displayName:'Skill '+i,description}));
  const ui=await fixture(r=>r(official),'api',{skills:rows});
  ui.run("currentId='thread';currentHost='local'");await ui.run('openSkills()');
  assert.equal(ui.nodes.get('skill-list').children[0].children[1].children[1].textContent,description);
  Object.assign(ui.nodes.get('skill-list'),{scrollHeight:30000,clientHeight:400,scrollTop:1000});
  ui.run('skillScroll()');assert.equal(ui.requests.filter(x=>x.includes('kind=skills')).length,1);
  ui.nodes.get('skill-list').scrollTop=29500;ui.run('skillScroll()');await new Promise(setImmediate);
  assert.equal(ui.run('skillPages.length'),220);
});

test('entering a gateway follows its language, and manual switching remains available',async()=>{
 const ui=await fixture(r=>r(official),'chatgpt',null,'en');assert.equal(ui.run('BridgeI18n.language()'),'en');
 ui.nodes.get('phone-language').value='zh';ui.nodes.get('phone-language').onchange();assert.equal(ui.run('BridgeI18n.language()'),'zh');
 await ui.run('start()');assert.equal(ui.run('BridgeI18n.language()'),'en');
 const chinese=await fixture(r=>r(official),'chatgpt',null,'zh');assert.equal(chinese.run('BridgeI18n.language()'),'zh');
});


test('account switch discards API model capabilities before opening the official picker',async()=>{
 const catalog={models:[{id:'gpt-6-astra',name:'Astra',efforts:[]}],modelSource:'api'};
 const ui=await fixture(r=>r(official),'api',catalog);
 ui.run("currentId='11111111-1111-4111-8111-111111111111';state={connected:true,model:'gpt-6-astra',effort:'minimal'}");
 await ui.nodes.get('model-button').onclick();
 catalog.modelSource='codex';catalog.models=[{id:'gpt-6-astra',name:'Astra',efforts:['low','medium','high','xhigh','max','ultra'],defaultEffort:'low'}];
 ui.run("accountsPanel.accept({accounts:[],activeId:'official',current:{kind:'chatgpt',name:'Official'},switch:{phase:'complete'}})");
 await ui.nodes.get('model-button').onclick();
 assert.deepEqual(ui.nodes.get('effort-select').children.map(o=>o.value),catalog.models[0].efforts);
 assert.equal(ui.nodes.get('effort-select').value,'low');
 await ui.nodes.get('model-form').onsubmit({preventDefault(){}});
 assert.equal(ui.responses.at(-1).effort,'low');
});
test('official model with unknown capabilities explains the disabled save until a supported model is selected',async()=>{
 const catalog={models:[{id:'official-missing',name:'Missing',efforts:[]},{id:'official-ready',name:'Ready',efforts:['high']}],modelSource:'codex'};
 const ui=await fixture(r=>r(official),'chatgpt',catalog);
 ui.run("currentId='11111111-1111-4111-8111-111111111111';state={connected:true,model:'official-missing',effort:'minimal'}");
 await ui.nodes.get('model-button').onclick();
 assert.equal(ui.nodes.get('effort-select').disabled,true);
 assert.equal(ui.nodes.get('effort-select').children.some(o=>o.value==='none'||o.value==='minimal'),false);
 assert.equal(ui.nodes.get('model-save').disabled,true);
 assert.match(ui.nodes.get('model-error').textContent,/未能读取此模型/);
 ui.nodes.get('model-select').value='official-ready';ui.nodes.get('model-select').onchange();
 assert.equal(ui.nodes.get('model-error').textContent,'');assert.equal(ui.nodes.get('model-save').disabled,false);
});
test('model default must belong to its supported efforts',async()=>{
 const catalog={models:[{id:'official-fixture',name:'Fixture',efforts:['low','high'],defaultEffort:'minimal'}],modelSource:'codex'};
 const ui=await fixture(r=>r(official),'chatgpt',catalog);
 ui.run("currentId='11111111-1111-4111-8111-111111111111';state={connected:true,model:'official-fixture',effort:'none'}");
 await ui.nodes.get('model-button').onclick();
 assert.equal(ui.nodes.get('effort-select').value,'low');
});


test('model picker uses the backend cache on every open and only explicit refresh bypasses it',async()=>{
 const catalog={models:[{id:'gpt-fixture',name:'GPT',efforts:[]}],modelSource:'api',modelAccessName:'Old access'};
 const ui=await fixture(r=>r(official),'api',catalog);
 ui.run("currentId='11111111-1111-4111-8111-111111111111';state={connected:true,model:'gpt-fixture',effort:'high'}");
 await ui.nodes.get('model-button').onclick();
 const modelReads=()=>ui.requests.filter(r=>r.includes('catalog?kind=models'));
 assert.equal(modelReads().length,1);assert.equal(modelReads()[0].includes('refresh=true'),false);
 ui.nodes.get('model-dialog').close();await ui.nodes.get('model-button').onclick();assert.equal(modelReads().length,2);
 assert.equal(modelReads()[1].includes('refresh=true'),false);
 catalog.models=[{id:'gemini-pro',name:'Gemini',efforts:[]}];catalog.modelAccessName='suopan-antigravity';
 await ui.nodes.get('model-refresh').onclick();
 assert.equal(ui.nodes.get('model-select').children.some(o=>o.value==='gpt-fixture'),false);
 assert.equal(ui.nodes.get('model-select').children.some(o=>o.value==='gemini-pro'),true);
 assert.equal(ui.responses.length,0);
 assert.equal(modelReads().length,3);assert.equal(modelReads()[2].includes('refresh=true'),true);
});


test('a provider change replaces its model list and discards an old in-flight response',async()=>{
 const catalog={models:[{id:'old',name:'Old'}],modelSource:'api'},ui=await fixture(r=>r(official),'api',catalog);
 ui.run("currentId='11111111-1111-4111-8111-111111111111';state={connected:true,provider:'old',model:'old'};var releaseOld;const readApi=api;api=(url,body)=>url.includes('catalog?kind=models')&&state.provider==='old'?new Promise(resolve=>releaseOld=resolve):readApi(url,body)");
 const pending=ui.nodes.get('model-button').onclick();
 catalog.models=[{id:'new',name:'New'}];ui.run("state.provider='new';state.model='new'");
 await ui.nodes.get('model-button').onclick();
 ui.run("releaseOld({modelSource:'api',models:[{id:'old',name:'Old'}]})");await pending;
 assert.deepEqual(ui.nodes.get('model-select').children.map(row=>row.value),['','new','__custom__']);
 assert.equal(ui.nodes.get('model-error').textContent,'');
});

test('reopening a failed model query reads the recovered backend result',async()=>{
 const catalog={models:[],modelSource:'api',modelError:'Temporary failure'},ui=await fixture(r=>r(official),'api',catalog);
 ui.run("currentId='11111111-1111-4111-8111-111111111111';state={connected:true,provider:'api'}");
 await ui.nodes.get('model-button').onclick();assert.match(ui.nodes.get('model-error').textContent,/Temporary failure/);
 delete catalog.modelError;catalog.models=[{id:'ready',name:'Ready'}];
 await ui.nodes.get('model-button').onclick();
 assert.equal(ui.requests.filter(url=>url.includes('catalog?kind=models')).length,2);
 assert.ok(ui.nodes.get('model-select').children.some(row=>row.value==='ready'));
 assert.doesNotMatch(ui.nodes.get('model-error').textContent,/Temporary failure/);
});
test('same chat reopens model rows before a delayed catalog read',async()=>{
 const catalog={models:[{id:'ready',name:'Ready',efforts:['low']}],modelSource:'codex',cache:{identity:'a'.repeat(64),updatedAt:1000}},ui=await fixture(r=>r(official),'chatgpt',catalog);
 ui.run("currentId='11111111-1111-4111-8111-111111111111';state={connected:true,provider:'chatgpt',model:'ready',modelCatalogIdentity:'"+'a'.repeat(64)+"'}");
 await ui.nodes.get('model-button').onclick();assert.match(ui.nodes.get('model-updated').textContent,/更新于：/);
 ui.run("var releaseCatalog;const originalApi=api;api=(url,body)=>url.includes('catalog?kind=models')?new Promise(resolve=>releaseCatalog=resolve):originalApi(url,body)");
 const pending=ui.nodes.get('model-button').onclick();assert.ok(ui.nodes.get('model-options').children.some(row=>row.children[0].value==='ready'));
 ui.run("releaseCatalog({models:[{id:'ready',name:'Ready',efforts:['low']}],modelSource:'codex',cache:{identity:'"+'a'.repeat(64)+"',updatedAt:1000}})");await pending;
 assert.match(ui.nodes.get('model-updated').textContent,/更新于：/);
});
test('a late catalog from another gateway cannot populate the current cache',async()=>{
 const identity='a'.repeat(64),catalog={models:[{id:'ready',efforts:['low']}],cache:{identity,updatedAt:1000}},ui=await fixture(r=>r(official),'chatgpt',catalog);
 ui.run("currentId='11111111-1111-4111-8111-111111111111';listIdentity='gateway-a';state={connected:true,provider:'chatgpt',model:'ready',modelCatalogIdentity:'"+identity+"'};var releaseGateway;const originalApi=api;api=(url,body)=>url.includes('catalog?kind=models')?new Promise(resolve=>releaseGateway=resolve):originalApi(url,body)");
 const pending=ui.nodes.get('model-button').onclick();ui.run("listIdentity='gateway-b';releaseGateway({models:[{id:'ready',efforts:['low']}],cache:{identity:'"+identity+"',updatedAt:1000}})");await pending;
 assert.equal(ui.run('modelCatalogs.size'),0);assert.equal(ui.run('catalogResults.models'),null);
});

test('legacy model fields are normalized and a failed directory ends the loading state',async()=>{
 const ui=await fixture(r=>r(official),'api',{modelSource:'api',models:[{model:'legacy',displayName:'Legacy name'},'string-model']});
 ui.run("currentId='11111111-1111-4111-8111-111111111111';state={connected:true,provider:'api'}");
 await ui.nodes.get('model-button').onclick();
 assert.ok(ui.nodes.get('model-select').children.some(row=>row.value==='legacy'&&row.textContent==='Legacy name'));
 assert.ok(ui.nodes.get('model-select').children.some(row=>row.value==='string-model'));
 ui.run("api=async()=>{throw Error('Offline')}");await ui.nodes.get('model-refresh').onclick();
 assert.ok(ui.nodes.get('model-options').children.some(row=>row.children[0].value==='legacy'),'failed refresh keeps visible rows');
 assert.match(ui.nodes.get('model-error').textContent,/Offline/);
 assert.equal(ui.nodes.get('model-save').disabled,true);assert.equal(ui.nodes.get('model-refresh').disabled,false);
});

test('a failed model save retains the selected effort and pending Fast preference',async()=>{
 const ui=await fixture(r=>r(official),'chatgpt',{modelSource:'codex',models:[{id:'model',name:'Model',efforts:['low','high'],fastTier:'priority'}],fastMode:{allowed:true,provider:'openai'}});
 ui.run("currentId='11111111-1111-4111-8111-111111111111';state={connected:true,provider:'openai',model:'model',effort:'high',serviceTier:'default'}");
 await ui.nodes.get('model-button').onclick();ui.nodes.get('effort-select').value='low';
 ui.nodes.get('fast-mode-enabled').checked=true;ui.nodes.get('fast-mode-enabled').onchange();
 ui.run("api=async()=>{throw Error('Save failed')}");await ui.nodes.get('model-form').onsubmit({preventDefault(){}});
 assert.equal(ui.nodes.get('effort-select').value,'low');assert.equal(ui.nodes.get('fast-mode-enabled').checked,true);
 assert.equal(ui.run('fastModeControl.payload().fastMode'),true);assert.match(ui.nodes.get('model-error').textContent,/Save failed/);
});

test('large model lists resolve display metadata with linear work',async()=>{
 const catalog={modelSource:'api',models:Array.from({length:1000},(_,i)=>({id:'model-'+i,name:'Model '+i}))};
 const ui=await fixture(r=>r(official),'api',catalog);
 ui.run("currentId='11111111-1111-4111-8111-111111111111';state={connected:true,provider:'api'}");
 await ui.nodes.get('model-button').onclick();
 const reads=ui.run("var idReads=0;for(const row of catalogData.models){const id=row.id;Object.defineProperty(row,'id',{get(){idReads++;return id;}});}renderModelChoices();idReads");
 assert.ok(reads<=catalog.models.length*3,'rendering should scale with the model count');
 assert.equal(ui.nodes.get('model-options').children.length,1001);
});
