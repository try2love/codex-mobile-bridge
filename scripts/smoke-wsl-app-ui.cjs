'use strict';
// Real-browser smoke on synthetic APIs only. Never connects to the user's gateway.
const fs=require('node:fs'),path=require('node:path'),http=require('node:http'),net=require('node:net'),assert=require('node:assert/strict');
const {spawn}=require('node:child_process');
const root=path.resolve(__dirname,'..'),delay=ms=>new Promise(r=>setTimeout(r,ms));
async function port(){const server=net.createServer();await new Promise(r=>server.listen(0,'127.0.0.1',r));const value=server.address().port;await new Promise(r=>server.close(r));return value;}
async function until(fn){let last;for(let i=0;i<120;i++){try{const value=await fn();if(value)return value;}catch(e){last=e;}await delay(100);}throw last||Error('Browser did not become ready');}
async function connect(url){
 const socket=new WebSocket(url),pending=new Map(),errors=[];let next=0;
 socket.addEventListener('message',event=>{const value=JSON.parse(event.data);if(value.method==='Runtime.exceptionThrown')errors.push(value.params.exceptionDetails.exception?.description||value.params.exceptionDetails.text);const request=pending.get(value.id);if(!request)return;pending.delete(value.id);value.error?request.reject(Error(value.error.message)):request.resolve(value.result);});
 await new Promise((resolve,reject)=>{socket.addEventListener('open',resolve,{once:true});socket.addEventListener('error',reject,{once:true});});
 const call=(method,params={})=>new Promise((resolve,reject)=>{const id=++next;pending.set(id,{resolve,reject});socket.send(JSON.stringify({id,method,params}));});
 return {call,errors,close:()=>socket.close(),async evaluate(expression){const result=await call('Runtime.evaluate',{expression,returnByValue:true,awaitPromise:true});if(result.exceptionDetails)throw Error(result.exceptionDetails.exception?.description||'Renderer evaluation failed');return result.result.value;}};
}
async function main(){
 const browser=process.env.CMB_BROWSER||'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe';assert.ok(fs.existsSync(browser),'Set CMB_BROWSER to a Chromium browser executable');
 const fixture=fs.mkdtempSync(path.join(root,'.tmp/wsl-app-ui-')),debugPort=await port(),wsl='wsl:'+'a'.repeat(32),sid='12345678-1234-4234-9234-123456789012';
 const assets=JSON.parse(fs.readFileSync(path.join(root,'web/assets.json'))),requests=[],unexpected=[];let scanned=false,enabled=false,cdp;
 for(const match of fs.readFileSync(path.join(root,'bridge/api/assets.py'),'utf8').matchAll(/"(\/vendor\/[^"]+)": \("([^"]+)"/g))assets[match[1]]=match[2];
 const clients=()=>({wslSupported:true,gatewayRunning:true,computer:'Synthetic Windows fixture',clients:[{id:'codex',name:'Codex',enabled:true,installed:true,configured:true,connected:true,running:true},{id:'claude',name:'Claude',enabled:false,installed:false,configured:false},{id:'deepseek',name:'DSH',enabled:false,installed:false,configured:false},...(scanned?[{id:wsl,name:'WSL Codex · Ubuntu · fixture',kind:'wsl-cli',icon:'codex',enabled,installed:true,configured:true,selectable:true,connected:enabled,running:enabled,canQuitDesktop:false,canForceQuit:false,reason:'使用原终端的 Codex 会话；账号与模型接入由 WSL 中的原工具管理。'}]:[])]});
 const models=[{id:'group/auto-gpt-6-1-sol',name:'Magpie 分组模型',efforts:['low','medium','high'],defaultEffort:'high'},{id:'group/id-only',name:'group/id-only',efforts:['medium'],defaultEffort:'medium'},{id:'group/another',name:'另一分组',efforts:['high'],defaultEffort:'high'}];
 const server=http.createServer(async(req,res)=>{
  const url=new URL(req.url,'http://fixture'),pathname=url.pathname;let body='';for await(const chunk of req)body+=chunk;body=body?JSON.parse(body):undefined;
  const json=value=>{res.writeHead(200,{'Content-Type':'application/json'});res.end(JSON.stringify(value));};
  if(pathname.startsWith('/api/')){
   requests.push({path:pathname,query:Object.fromEntries(url.searchParams),body});
   if(pathname==='/api/auth')return json({authenticated:true,csrf:'fixture',uiLanguage:'zh',computer:{name:'Synthetic Windows fixture',platform:'win32'}});
   if(pathname==='/api/clients'){if(body?.action==='scan-wsl')scanned=true;else if(body){assert.equal(body.provider,wsl);assert.equal(body.quitDesktop||false,false);enabled=body.enabled;}return json(clients());}
   if(pathname==='/api/accounts')return json({accounts:[],current:{status:'ready',kind:'external',name:'Magpie'},scope:url.searchParams.has('entry')?'wsl':'desktop',management:{external:true},canSwitch:false,blockers:[]});
   if(pathname==='/api/account')return json({visible:false});
   if(pathname==='/api/activity')return json({sessions:[]});
   if(pathname==='/api/codex/entries'&&body?.action==='connect'){assert.equal(body.id,wsl);return json({ok:true,host:wsl});}
   if(pathname==='/api/projects/browse'){assert.equal(url.searchParams.get('entry'),wsl);assert.equal(body.host,wsl);const selected=body.name?body.path+'/'+body.name:body.path||'/home/fixture';if(body.name)assert.deepEqual(body.identity,{cwd:body.path,device:1,inode:42});return json({path:selected,parent:selected==='/home/fixture'?'/home':'/home/fixture',home:'/home/fixture',roots:[{name:'/',path:'/'}],identity:{cwd:selected,device:1,inode:42},directories:selected==='/home/fixture'?[{name:'works',path:'/home/fixture/works'}]:[]});}
   if(pathname==='/api/projects'){assert.equal(url.searchParams.get('entry'),wsl);if(body){assert.deepEqual(body,{path:'/home/fixture/works/new fixture',createDirectory:false,host:wsl});return json({project:{key:wsl+'|directory:fixture',cwd:'/home/fixture/works/new fixture',host:wsl,name:'new fixture'}});}return json({projects:[{key:wsl+'|/fixture',name:'fixture',host:wsl,hostLabel:'WSL fixture',cwd:'/fixture'}]});}
   if(pathname==='/api/sessions'&&body){assert.equal(url.searchParams.get('entry'),wsl);assert.ok([wsl+'|independent',wsl+'|directory:fixture'].includes(body.project));assert.deepEqual(Object.keys(body).sort(),['id','project','title']);return json({id:sid,host:wsl,opened:true,message:'聊天已创建，可以直接发送消息'});}
   if(pathname==='/api/sessions'&&url.searchParams.get('q')==='paging-fixture'){const offset=Number(url.searchParams.get('offset'));return json({sessions:Array.from({length:101},(_,i)=>({id:'12345678-1234-4234-9234-'+String(i).padStart(12,'0'),host:wsl,title:'Paging fixture '+i,projectName:'fixture',projectKey:'fixture',cwd:'/fixture',recency:101-i})).slice(offset,offset+100),unavailableHosts:[]});}
   if(pathname==='/api/sessions')return json({sessions:[{id:sid,host:url.searchParams.get('entry')===wsl?wsl:'local',hostLabel:'Synthetic fixture',title:url.searchParams.get('entry')===wsl?'WSL fixture conversation':'Windows fixture conversation',projectName:'fixture',projectKey:'fixture',cwd:'/fixture',recency:Date.now()}],unavailableHosts:[]});
   if(pathname.startsWith('/api/sessions/'+sid+'/')){
    const meta={id:sid,host:url.searchParams.get('host')||wsl,hostLabel:'WSL fixture',title:'WSL fixture conversation',cwd:'/fixture',model:models[0].id,provider:'magpie',effort:'high',requests:[],status:'idle',connected:true,historyComplete:true,capabilities:{send:true,stop:true,settings:true,workspace:true,attachments:true,permissions:false,skills:false,notifications:false,queue:false,plan:false,goal:false,terminal:false,sideChat:false,messageActions:false}};
    if(pathname.endsWith('/timeline')||pathname.endsWith('/changes'))return json({sequence:0,epoch:'fixture',rows:[],files:[],hasMore:false,meta});
    if(pathname.endsWith('/reconnect'))return json(meta);
    if(pathname.endsWith('/catalog'))return json({kind:'models',models,currentModel:models[0].id,currentEffort:'high',modelSource:'codex',fastMode:{allowed:false}});
    if(pathname.endsWith('/notifications'))return json({enabled:false});
   }
   if(pathname==='/api/notifications/defaults')return json({requests:true,completions:false});
   unexpected.push(req.method+' '+req.url);res.writeHead(404);return res.end(JSON.stringify({error:'Unexpected fixture API'}));
  }
  const files=assets[pathname]||(/^\/client-icons\/(codex|claude|deepseek)\.png$/.test(pathname)?pathname.slice(1):null);
  if(files){const names=Array.isArray(files)?files:[files];const data=names.map(file=>fs.readFileSync(path.join(root,'web',file)));res.setHeader('Content-Type',pathname.endsWith('.js')?'text/javascript':pathname.endsWith('.css')?'text/css':pathname.endsWith('.png')?'image/png':'text/html; charset=utf-8');return res.end(Buffer.concat(data.map((item,i)=>i&&Array.isArray(files)?Buffer.concat([Buffer.from('\n'),item]):item)));}
  res.writeHead(404);res.end();
 });
 await new Promise(r=>server.listen(0,'127.0.0.1',r));const origin='http://127.0.0.1:'+server.address().port;
 const child=spawn(browser,['--headless=new','--no-first-run','--no-default-browser-check','--disable-gpu','--disable-extensions','--remote-debugging-port='+debugPort,'--user-data-dir='+path.join(fixture,'profile'),'about:blank'],{windowsHide:true,stdio:'ignore'});
 try{
  const targets=await until(async()=>{const response=await fetch('http://127.0.0.1:'+debugPort+'/json/list');const value=await response.json();return value.find(item=>item.type==='page');});cdp=await connect(targets.webSocketDebuggerUrl);
  await cdp.call('Runtime.enable');await cdp.call('Page.enable');await cdp.call('Emulation.setDeviceMetricsOverride',{width:492,height:850,deviceScaleFactor:1,mobile:true});await cdp.call('Page.navigate',{url:origin});
  await until(()=>cdp.evaluate('typeof clientNavigation!=="undefined"&&clientNavigation.clients.length===3'));
  await cdp.evaluate(fs.readFileSync(path.join(root,'tests/browser/codex-entries.browser.js'),'utf8'));
  const result=await cdp.evaluate('checkCodexEntries()');assert.equal(result.passed,30);
  await cdp.evaluate('(async()=>{clientNavigation.manager?.dialog.close();await clientNavigation.toggle("'+wsl+'",true);await clientNavigation.choose("'+wsl+'");await document.getElementById("new-chat").onclick();document.getElementById("new-chat-project").value="__directory__";document.getElementById("new-chat-project").onchange();await document.getElementById("new-chat-directory-browse").onclick();})()');
  const pickerScreenshot=await cdp.call('Page.captureScreenshot',{format:'png'});fs.writeFileSync(path.join(root,'.tmp/wsl-folder-picker.png'),Buffer.from(pickerScreenshot.data,'base64'));
  await cdp.evaluate('document.getElementById("folder-picker-select").onclick()');
  const directoryScreenshot=await cdp.call('Page.captureScreenshot',{format:'png'});fs.writeFileSync(path.join(root,'.tmp/wsl-app-directory.png'),Buffer.from(directoryScreenshot.data,'base64'));
  await cdp.evaluate('document.getElementById("new-chat-dialog").close()');
  await cdp.evaluate('(async()=>{clientNavigation.manager?.dialog.close();await clientNavigation.toggle("'+wsl+'",true);await clientNavigation.choose("'+wsl+'");await openChat("'+sid+'","'+wsl+'");await document.getElementById("model-button").onclick();})()');
  const screenshot=await cdp.call('Page.captureScreenshot',{format:'png'});fs.writeFileSync(path.join(root,'.tmp/wsl-app-models.png'),Buffer.from(screenshot.data,'base64'));
  await cdp.call('Emulation.setDeviceMetricsOverride',{width:1280,height:900,deviceScaleFactor:1,mobile:false});assert.equal(await cdp.evaluate('document.documentElement.scrollWidth<=innerWidth'),true);
  assert.deepEqual(cdp.errors,[]);assert.deepEqual(unexpected,[]);assert.equal(requests.some(row=>row.path.endsWith('/send')||row.path.endsWith('/respond')||row.path.endsWith('/stop')),false);
  const creationRequests=requests.filter(row=>row.path==='/api/sessions'&&row.body).length;assert.equal(creationRequests,2);
  const summary={...result,wideViewport:1280,browserErrors:cdp.errors,unexpectedAPI:unexpected,creationRequests,executionRequests:0};fs.writeFileSync(path.join(root,'.tmp/wsl-app-ui-result.json'),JSON.stringify(summary,null,2));console.log(JSON.stringify(summary));
 }finally{cdp?.close();child.kill();server.closeAllConnections();await new Promise(r=>server.close(r));}
}
main().catch(error=>{console.error(error);process.exitCode=1;});
