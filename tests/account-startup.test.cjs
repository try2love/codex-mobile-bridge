'use strict';
const test=require('node:test'),assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const official={visible:true,loginType:'chatgpt',accountKey:'a'.repeat(64),limits:[],resetCredits:null,canReset:false,updatedAt:1900000000};

async function fixture(read,kind='chatgpt',catalog=null){
  const nodes=new Map(),timers=new Map(),requests=[],intervals=[],events={},responses=[];let timerId=0,delayNextSkill=null,failNextSkill=false;
  const node=(tag='div')=>({tagName:tag.toUpperCase(),value:'',checked:false,disabled:false,hidden:false,open:false,textContent:'',dataset:{},options:[],children:[],style:{},scrollTop:0,parentElement:{},
    classList:{add(){},remove(){},toggle(){}},addEventListener(){},setAttribute(k,v){this[k]=v;},closest(selector){return selector==='dialog'?nodes.get('accounts-dialog'):null;},
    replaceChildren(...children){this.children=children;},append(...children){this.children.push(...children);},querySelectorAll(){return [];},querySelector(){return null;},
    showModal(){this.open=true;},close(){this.open=false;}});
  const html=fs.readFileSync(path.join(__dirname,'../web/index.html'),'utf8');
  for(const match of html.matchAll(/<([a-z]+)\b([^>]*\bid="([^"]+)"[^>]*)>/g)){
    const n=node(match[1]);n.hidden=/\bhidden\b/.test(match[2]);nodes.set(match[3],n);
  }
  const storage=()=>({getItem(){return null;},setItem(){},clear(){}});
  const response=(data,status=200)=>({ok:status===200,status,json:async()=>data});
  const context=vm.createContext({Date,Option:function(text,value){return {...node('option'),textContent:text,value};},document:{addEventListener(){},documentElement:{},getElementById:id=>nodes.get(id),querySelector:()=>({classList:{toggle(){}}}),querySelectorAll:()=>[],createElement:node},
    window:{addEventListener:(name,fn)=>events[name]=fn},localStorage:storage(),sessionStorage:storage(),
    location:{hash:'',pathname:'/',search:''},history:{replaceState(){}},
    setTimeout(fn,delay){timers.set(++timerId,{fn,delay});return timerId;},clearTimeout(id){timers.delete(id);},setInterval(fn,delay){intervals.push({fn,delay});},
    fetch:async(url,options={})=>{
      requests.push(url);
      if(url==='/api/auth')return response({authenticated:true,csrf:'fixture'});
      if(url.startsWith('/api/sessions?'))return response({sessions:[]});
      if(url==='/api/account'||url==='/api/account?cached=1')return read(response);
      if(url==='/api/accounts')return response({accounts:[],activeId:null,current:{kind,status:'ready',name:'Fixture'},switch:{phase:'idle'}});
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
  for(const file of ['web/i18n.js','web/account.js','web/accounts.js','web/modes.js','web/attachments.js','web/activity.js','web/fast-mode.js','web/message-actions.js','web/app.js'])vm.runInContext(fs.readFileSync(path.join(__dirname,'..',file),'utf8'),context);
  await new Promise(setImmediate);
  return {nodes,requests,timers,intervals,events,responses,run:code=>vm.runInContext(code,context),
    failNextSkill(){failNextSkill=true;},
    delayNextSkill(){let release;const promise=new Promise(resolve=>release=resolve);delayNextSkill={promise,release};return release;},
    async retry(){const entry=[...timers.entries()].find(([,t])=>t.delay<=5000);assert.ok(entry,'Account read should retry promptly without user input');timers.delete(entry[0]);await entry[1].fn();await new Promise(setImmediate);}};
}

test('official and API connections share one fixed clickable entry without querying quota on startup',async()=>{
 for(const [kind,label] of [['chatgpt','账号与额度'],['api','API 接入']]){
  const ui=await fixture(r=>r(official),kind);
  assert.equal(ui.nodes.get('accounts-button').textContent,label);
  assert.equal(ui.nodes.has('account-status'),false);assert.equal(ui.nodes.has('account-dialog'),false);
  assert.equal(ui.requests.filter(url=>url==='/api/account').length,0);
  ui.nodes.get('accounts-button').onclick();await new Promise(setImmediate);
  assert.equal(ui.nodes.get('accounts-dialog').open,true);
  assert.equal(ui.nodes.get('account-button').hidden,kind!=='chatgpt');
  assert.equal(ui.requests.filter(url=>url==='/api/account').length,0);
 }
});
test('quota updates on opening details, and only after five minutes while visible',async()=>{
 const ui=await fixture(r=>r(official));
 ui.nodes.get('accounts-button').onclick();await new Promise(setImmediate);
 ui.nodes.get('account-details').open=true;ui.nodes.get('account-details').ontoggle();await new Promise(setImmediate);
 const quota=[...ui.timers.values()].find(i=>i.delay>299000);assert.ok(quota);
 assert.equal(ui.requests.filter(u=>u==='/api/account').length,1);
 ui.run('accountPanel.checkedAt=Date.now()-300001');quota.fn();await new Promise(setImmediate);
 assert.equal(ui.requests.filter(u=>u.startsWith('/api/account')&&!u.startsWith('/api/accounts')).length,2);
 ui.run('accountPanel.checkedAt=0;document.hidden=true');quota.fn();
 ui.run('document.hidden=false');ui.nodes.get('accounts-dialog').open=false;quota.fn();
 ui.events.focus();await new Promise(setImmediate);assert.equal(ui.requests.filter(u=>u.startsWith('/api/account')&&!u.startsWith('/api/accounts')).length,2);
});
test('read failure does not start repeated automatic quota queries',async()=>{
 const ui=await fixture(r=>r({error:'offline'},409));
 ui.nodes.get('account-details').open=true;ui.nodes.get('account-details').ontoggle();await new Promise(setImmediate);
 assert.equal(ui.requests.filter(u=>u==='/api/account').length,1);
 assert.equal([...ui.timers.values()].filter(t=>t.delay<2000).length,0);
 ui.run('showLogin()');assert.equal(ui.nodes.get('account-button').hidden,true);
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
 assert.equal(ui.responses.length,0);select.value='gemini-fixture';select.onchange();
 assert.equal(ui.nodes.get('model-save').disabled,false);assert.equal(ui.nodes.get('fast-mode-control').hidden,true);
 await ui.nodes.get('model-form').onsubmit({preventDefault(){}});
 assert.equal(ui.responses[0].model,'gemini-fixture');assert.equal(ui.responses[0].effort,'high');
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
  const catalog={models:[{id:'fixture-model',name:'Fixture',efforts:[]}],skills:[],modelSource:'api',fastMode:{allowed:false}};
  const ui=await fixture(r=>r(official),'api',catalog);
  ui.run("currentId='11111111-1111-4111-8111-111111111111';currentHost='local';state={connected:true,model:'fixture-model'}");
  ui.run("catalogData.models=null;catalogTasks.models=Promise.resolve({data:[{id:'legacy-model',name:'Legacy'}],modelSource:'api'}).then(value=>{catalogData.models=Array.from(value.data);return {...value,kind:'models',models:catalogData.models}})");
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
