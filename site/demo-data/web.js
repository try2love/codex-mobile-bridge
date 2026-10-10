'use strict';
(()=>{
const T=window.demoText,ids=['11111111-1111-4111-8111-111111111111','22222222-2222-4222-8222-222222222222'];
const sessions=ids.map((id,i)=>({id,host:'local',hostLabel:T('此电脑','This computer'),title:i?T('整理项目文档','Organize project documentation'):T('优化产品首页','Refine the product homepage'),cwd:'/projects/website',projectName:'website',projectKey:'local|website',recency:Date.now()-i*600000}));
const base={connected:true,status:'idle',model:'gpt-6.1-sol',provider:'openai',effort:'high',permissionMode:'ask',contextUsage:{usedTokens:24800,contextWindow:200000},requests:[],submissions:[],historyComplete:true,serviceTier:'default',collaborationMode:'default'};
const meta=new Map(ids.map(id=>[id,{...base}]));
let sequence=1,side={id:null,connected:false,turns:[],requests:[]},branch='feature/homepage',changes=[{path:'README.md',index:' ',worktree:'M',staged:false,unstaged:true,untracked:false,conflict:false},{path:'src/app.py',index:'M',worktree:' ',staged:true,unstaged:false,untracked:false,conflict:false}];
const rows=new Map(ids.map(id=>[id,[{key:'u1',role:'user',text:T('整理一下首页优化的进度。','Summarize the homepage improvements.')},{key:'a1',role:'assistant',text:T('已经整理了导航和首屏文案。\n\n你可以打开 **文件** 检查源码，在 **Git** 面板审阅改动，或新建 **侧边聊天** 讨论另一个问题。\n\n```python\nprint("Hello, Bridge")\n```','Navigation and hero copy are ready.\n\nOpen **Files** to inspect the source, review changes in **Git**, or start a **side chat** to explore another question.\n\n```python\nprint("Hello, Bridge")\n```')}]]));
const files={'README.md':'# Website\n\nWelcome to the demo project.','src/app.py':'from pathlib import Path\n\nprint(Path.cwd().name)\n','deploy.cmd':'@echo off\npython src\\app.py\n','docs/notes.md':'# Notes\n\nReview the homepage on mobile.'};
const skills=[{id:'review',name:'review',displayName:'Review',description:T('审阅代码变更与需求一致性','Review code changes against requirements')},{id:'docs',name:'docs',displayName:'Docs',description:T('整理项目文档','Organize project documentation')}];
const catalog=m=>({currentModel:m.model,currentEffort:m.effort,currentServiceTier:m.serviceTier,modelSource:'codex',fastMode:{allowed:true,defaultServiceTier:'default'},models:[{id:'gpt-6.1-sol',name:'GPT-6.1 Sol',efforts:['low','medium','high','xhigh'],defaultEffort:'high',fastTier:'priority'},{id:'gpt-6-astra',name:'GPT-6 Astra',efforts:['low','medium','high'],defaultEffort:'high'}],skills,total:skills.length,errors:[]});
const page=id=>({epoch:'demo',sequence:sequence++,rows:(rows.get(id)||[]).map((r,i)=>({...r,order:i,version:String(i)})),hasMore:false,before:null,files:[],meta:{...sessions.find(s=>s.id===id),...meta.get(id)}});
const history=[{id:'a'.repeat(40),parents:['b'.repeat(40)],subject:T('优化产品首页','Refine the homepage'),author:'try2love',date:'2026-10-07',refs:branch},{id:'b'.repeat(40),parents:['c'.repeat(40)],subject:T('添加项目文件预览','Add project file previews'),author:'try2love',date:'2026-10-06',refs:'main'},{id:'c'.repeat(40),parents:[],subject:T('初始化项目','Initialize project'),author:'try2love',date:'2026-10-05',refs:''}];
const terms=new Map();
const response=data=>new Response(JSON.stringify(data),{headers:{'Content-Type':'application/json'}});
const unsupported=()=>new Response(JSON.stringify({error:T('此操作未在演示中开放；不会修改真实数据。','This operation is unavailable in the demo; real data is never changed.')}),{status:400});
window.fetch=async(input,options={})=>{
 const url=new URL(input,location.href),p=url.pathname,b=typeof options.body==='string'?JSON.parse(options.body):{},id=/sessions\/([^/]+)/.exec(p)?.[1]||ids[0],m=meta.get(id)||base;
 if(options.signal?.aborted)throw new DOMException('Aborted','AbortError');
 if(p==='/api/clients'){if(b.provider){const row=demoClients.find(c=>c.id===b.provider);if(row)Object.assign(row,{enabled:b.enabled,running:b.enabled,connected:b.enabled});}return response({clients:demoClients,gatewayRunning:true});}
 if(p.startsWith('/api/desktop-sessions/')){const [provider,action]=p.slice('/api/desktop-sessions/'.length).split('/');return response(demoClientRequest(provider,action,b,url.searchParams.get('sessionId')));}
 if(p==='/api/file-transfer')return response({clickDownloadMiB:100});
 if(p==='/api/auth')return response({authenticated:true,csrf:'demo',instanceId:'demo',passwordless:false,transport:'poll',computer:{name:'try2love · Mac',platform:'Darwin'}});
 if(p==='/api/accounts')return response(await fixtureAccountRequest({action:'list'}));
 if(p==='/api/account')return response(fixtureUsage());
 if(p==='/api/accounts/details')return response(await fixtureAccountRequest({action:'details',...b}));
 if(p==='/api/accounts/switch')return response(await fixtureAccountRequest({action:'switch',...b}));
 if(p==='/api/projects')return response({projects:[{key:'local|website',name:'website',cwd:'/projects/website',host:'local'}]});
 if(p==='/api/sessions'&&options.method==='POST'){const id=crypto.randomUUID();sessions.unshift({...sessions[0],id,title:b.title||T('新聊天','New chat')});meta.set(id,{...base});rows.set(id,[]);return response({id,host:'local',message:T('聊天已创建','Chat created')});}
 if(p==='/api/sessions')return response({sessions:sessions.filter(s=>s.title.toLowerCase().includes((url.searchParams.get('q')||'').toLowerCase())),unavailableHosts:[]});
 if(p==='/api/activity')return response({sessions:sessions.map(s=>({...s,status:'idle',connected:true,turnId:'demo',turnStatus:'completed'}))});
 if(p.endsWith('/timeline'))return response(page(id));
 if(p.endsWith('/changes')){await new Promise((resolve,reject)=>{const timer=setTimeout(resolve,900);options.signal?.addEventListener('abort',()=>{clearTimeout(timer);reject(new DOMException('Aborted','AbortError'));},{once:true});});return response(page(id));}
 if(p.endsWith('/catalog')||p.endsWith('/skills'))return response(catalog(m));
 if(p.endsWith('/permissions')){m.permissionMode=b.preset;return response({confirmed:true});}
 if(p.endsWith('/settings')){Object.assign(m,{model:b.model,effort:b.effort,serviceTier:b.fastMode?'priority':'default'});return response({confirmed:true,applied:true});}
 if(p.endsWith('/send')){rows.get(id).push({key:crypto.randomUUID(),role:'user',text:b.text},{key:crypto.randomUUID(),role:'assistant',text:T('收到。这是一条示例回复，你可以继续浏览工作台或调整会话设置。','Received. This is a sample reply. Continue exploring the workbench or change the chat settings.')});return response({status:'accepted',id:b.id});}
 if(p.endsWith('/notifications'))return response({available:true,watching:false,notifyOnCompletion:false});
 if(p==='/api/notifications/settings')return response({requests:false,completions:false});
 if(p.endsWith('/side-chat')){
  if(b.action==='create')side={...base,id:crypto.randomUUID(),turns:[],requests:[],submissions:[]};
  if(b.action==='close')side={id:null,connected:false,turns:[],requests:[]};
  if(b.action==='permissions')side.permissionMode=b.preset;
  if(b.action==='settings')Object.assign(side,{model:b.model,effort:b.effort});
  if(b.action==='catalog'||b.action==='skills')return response(catalog(side));
  if(b.action==='send'){side.turns.push({id:crypto.randomUUID(),status:'completed',messages:[{role:'user',text:b.text},{role:'assistant',text:T('可以先检查页面层级，再逐项调整留白和按钮位置。','Start with the page hierarchy, then refine spacing and button placement.')} ]});side.submissions.push({id:b.submissionId,status:'accepted'});side.collaborationMode=b.collaborationMode||'default';}
  return response(side);
 }
 if(p.endsWith('/terminal')){
  const tid=b.id||url.searchParams.get('id');if(!tid)return response({mode:'pty',shell:'/bin/zsh',cwd:'/projects/website'});
  if(b.action==='open')terms.set(tid,{output:'\x1b[90m'+T('演示终端：命令不会在电脑执行。','Demo terminal: commands do not run on a computer.')+'\x1b[0m\r\ntry2love@demo website % ',cursor:1,input:'',running:true});
  const term=terms.get(tid);if(!term)return unsupported();
  if(b.action==='close')term.running=false;
  if(b.action==='input'){for(const ch of b.data){if(ch==='\r'){const cmd=term.input.trim();term.output+='\r\n'+(cmd==='pwd'?'/projects/website':cmd==='ls'?'README.md  src  docs  deploy.cmd':cmd.startsWith('git status')?'On branch '+branch+'\r\nChanges: README.md, src/app.py':T('演示支持 pwd、ls、git status。','Try pwd, ls or git status in this demo.'))+'\r\ntry2love@demo website % ';term.input='';}else if(ch==='\x03'){term.output+='^C\r\ntry2love@demo website % ';term.input='';}else{term.input+=ch;term.output+=ch;}}term.cursor++;}
  const output=term.output;if(!b.action||b.action==='open')term.output='';return response({running:term.running,shell:'/bin/zsh',cwd:'/projects/website',cursor:term.cursor,output:b.action==='input'||b.action==='resize'?'':output});
 }
 const path=url.searchParams.get('path')||'';
 if(p.endsWith('/workspace')){const names=Object.keys(files).filter(name=>name.startsWith(path?path+'/':'')&&!name.slice(path.length+(path?1:0)).includes('/'));const dirs=path?[]:['src','docs'];const entries=[...dirs.map(name=>({name,path:name,kind:'directory'})),...names.map(name=>({name:name.split('/').at(-1),path:name,kind:'file',size:files[name].length}))].filter(e=>e.name.includes(url.searchParams.get('search')||''));return response({path,project:'website',entries,total:entries.length,nextOffset:null});}
 if(p.endsWith('/workspace/preview'))return response({name:path.split('/').at(-1),size:(files[path]||'').length,kind:'text',text:files[path]||''});
 if(p.endsWith('/git-status'))return response({available:true,branch,commit:history[0].id,scope:'.',project:'website',version:String(sequence),entries:changes,operation:null});
 if(p.endsWith('/git-history'))return response({commits:history,hasMore:false});
 if(p.endsWith('/git-diff')||p.endsWith('/git-history-diff'))return response({path,diff:'diff --git a/'+path+' b/'+path+'\n--- a/'+path+'\n+++ b/'+path+'\n@@ -1,1 +1,1 @@\n-# Website\n+# Mobile workbench\n',text:'@@ -1 +1 @@\n-# Website\n+# Mobile workbench\n'});
 if(p.endsWith('/git-commit'))return response({message:history.find(c=>c.id===url.searchParams.get('revision'))?.subject||'Demo commit',author:'try2love',date:'2026-10-07',id:history[0].id,parents:history[0].parents,entries:[{path:'README.md',change:'M'}]});
 if(p.endsWith('/git-branches'))return response({branches:[branch,'main'].filter((v,i,a)=>a.indexOf(v)===i).map(name=>({name,id:history[0].id,current:name===branch}))});
 if(p.endsWith('/git-action')){if(b.action==='stage'||b.action==='unstage'){for(const entry of changes.filter(e=>!b.path||e.path===b.path)){entry.staged=b.action==='stage';entry.unstaged=!entry.staged;entry.index=entry.staged?'M':' ';entry.worktree=entry.staged?' ':'M';}}else if(b.action==='commit'){if(!changes.some(e=>e.staged))return unsupported();history.unshift({id:crypto.randomUUID().replaceAll('-','').padEnd(40,'a'),parents:[history[0].id],subject:b.message,author:'try2love',date:'2026-10-07',refs:branch});changes=changes.filter(e=>!e.staged);}else if(b.action==='switch-branch'||b.action==='create-branch')branch=b.branch;else return unsupported();return response({ok:true});}
 if(p.endsWith('/subagents')&&!url.searchParams.get('id'))return response({agents:[{id:ids[1],parentId:id,title:T('检查移动布局','Review mobile layout'),nickname:'layout-review',role:'reviewer',path:'/root/layout',archived:false}]});
 if(p.endsWith('/subagents')&&url.searchParams.get('id'))return response({historyComplete:true,turns:[{status:'completed',messages:[{role:'assistant',text:T('已检查手机宽度下的导航和输入区。','Reviewed navigation and composer at mobile widths.')}]}]});
 if(p.endsWith('/reconnect')||p.endsWith('/history'))return response({ok:true});
 return unsupported();
};
window.demoIds=ids;
})();
