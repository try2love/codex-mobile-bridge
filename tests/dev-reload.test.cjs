'use strict';
const test=require('node:test'),assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const {sourceFiles,displayState,createDevelopmentReload}=require('../desktop/shared/dev-reload.cjs');
const {create}=require('../desktop/shell/dev-reload.js');
const root=path.resolve(__dirname,'..');

function timers(){
  const jobs=new Set();
  return {
    set(fn,delay){const job={fn,delay,unref(){}};jobs.add(job);return job;},
    clear(job){jobs.delete(job);},
    run(delay){for(const job of [...jobs])if(job.delay===delay){jobs.delete(job);job.fn();}},
    get size(){return jobs.size;}
  };
}
function sourceFixture(t,enabled=true){
  const base=path.join(root,'.tmp/dev-reload-tests');fs.mkdirSync(base,{recursive:true});
  const directory=fs.mkdtempSync(path.join(base,'case-'));
  for(const file of ['desktop/index.html','desktop/shell/ui.js','desktop/shell/ui.css','desktop/main.cjs','web/features/accounts/wsl-accounts.js']){
    fs.mkdirSync(path.dirname(path.join(directory,file)),{recursive:true});fs.writeFileSync(path.join(directory,file),'initial');
  }
  fs.writeFileSync(path.join(directory,'desktop/index.html'),'<link rel="stylesheet" href="shell/ui.css"><script src="shell/ui.js"></script>');
  t.after(()=>{
    const target=path.resolve(directory);
    assert.ok(target.startsWith(path.resolve(base)+path.sep));
    fs.rmSync(target,{recursive:true,force:true});
  });
  const clock=timers(),notifications=[];let idle=true,reloads=0;
  const watcher=createDevelopmentReload({root:directory,enabled,notify:value=>notifications.push(value),reload:()=>reloads++,canReload:()=>idle,
    setTimer:clock.set,clearTimer:clock.clear,setPoll:()=>({unref(){}}),clearPoll(){}});
  t.after(()=>watcher.close());
  return {directory,watcher,clock,notifications,edit(file){fs.appendFileSync(path.join(directory,file),' updated');watcher.poll();},
    set idle(value){idle=value;},get reloads(){return reloads;}};
}
const view={tab:'network',scroll:321,details:['cloudflared-repair']};

test('development source list contains rendered assets and the lazy WSL script, not backend or private paths',t=>{
  const f=sourceFixture(t);
  fs.appendFileSync(path.join(f.directory,'desktop/index.html'),'<script src="main.cjs"></script><link href="../.local/secret.css"><script src="../../outside.js"></script><script src="https://example.test/app.js"></script>');
  const files=[...sourceFiles(f.directory)].map(file=>path.relative(f.directory,file).replaceAll('\\','/')).sort();
  assert.deepEqual(files,['desktop/index.html','desktop/shell/ui.css','desktop/shell/ui.js','web/features/accounts/wsl-accounts.js']);
});
test('development watcher debounces stylesheet saves without requesting a renderer reload',t=>{
  const f=sourceFixture(t);f.edit('desktop/shell/ui.css');f.edit('desktop/shell/ui.css');
  assert.equal(f.notifications.length,0);f.clock.run(200);
  assert.equal(f.notifications.length,1);assert.equal(f.notifications[0].stylesOnly,true);
  assert.equal(f.watcher.state().pending,null);assert.equal(f.reloads,0);
  f.edit('desktop/main.cjs');f.clock.run(200);assert.equal(f.notifications.length,1);
});
test('HTML and JavaScript changes queue a renderer-only reload, preserve a safe view, and ignore stale revisions',t=>{
  const f=sourceFixture(t);f.edit('desktop/shell/ui.js');f.edit('desktop/index.html');f.clock.run(200);
  const change=f.notifications[0];assert.equal(change.stylesOnly,false);
  assert.deepEqual(f.watcher.request({revision:change.revision-1,view}),{accepted:false});
  assert.deepEqual(f.watcher.request({revision:change.revision,view}),{accepted:true});
  assert.equal(f.reloads,0);f.clock.run(0);assert.equal(f.reloads,1);
  assert.equal(f.watcher.state().reloaded,true);assert.deepEqual(f.watcher.state().view,view);
  assert.equal(f.watcher.state().pending,null);
});
test('pending worker operations defer reload and are rechecked immediately before replacing the renderer',t=>{
  const f=sourceFixture(t);f.edit('desktop/shell/ui.js');f.clock.run(200);const revision=f.notifications[0].revision;
  f.idle=false;assert.deepEqual(f.watcher.request({revision,view}),{accepted:false});
  f.idle=true;assert.deepEqual(f.watcher.request({revision,view}),{accepted:true});f.idle=false;f.clock.run(0);
  assert.equal(f.reloads,0);assert.ok(f.watcher.state().pending);assert.equal(f.watcher.state().reloaded,false);
});
test('disabled watcher has no automatic reloads and close cancels all scheduled work',t=>{
  const disabled=sourceFixture(t,false);disabled.edit('desktop/shell/ui.js');disabled.clock.run(200);assert.equal(disabled.notifications.length,0);
  const f=sourceFixture(t);f.edit('desktop/shell/ui.js');f.watcher.close();f.clock.run(200);
  assert.equal(f.notifications.length,0);assert.equal(f.reloads,0);
});
test('view transfer accepts only display state and never input, credentials or arbitrary tab names',()=>{
  assert.deepEqual(displayState(view),view);
  for(const invalid of [{...view,password:'secret'},{...view,tab:'arbitrary'},{...view,scroll:Infinity},{...view,details:['../../file']},{...view,details:'not-an-array'}])assert.throws(()=>displayState(invalid));
});

function preload(argv=[]){
  let api,listener;const calls=[],requests=[];
  const ipc={invoke(channel,...args){calls.push([channel,...args]);return new Promise((resolve,reject)=>requests.push({resolve,reject}));},on(channel,value){listener=value;},removeListener(channel,value){assert.equal(value,listener);listener=null;}};
  vm.runInNewContext(fs.readFileSync(path.join(root,'desktop/preload.cjs'),'utf8'),{require:()=>({ipcRenderer:ipc,contextBridge:{exposeInMainWorld(name,value){api=value;}}}),process:{platform:'win32',argv}});
  return {api,calls,requests,emit(event,value){listener(event,value);},get listener(){return listener;}};
}
test('normal and packaged-style preload arguments expose no development IPC controls',()=>{
  assert.equal(preload().api.development,undefined);
  assert.equal(preload(['--dev-ui']).api.development,undefined);
  const source=fs.readFileSync(path.join(root,'desktop/main.cjs'),'utf8');
  assert.match(source,/developmentUI=!app\.isPackaged&&process\.argv\.includes\('--dev-ui'\)/);
  assert.match(source,/additionalArguments:developmentUI\?\['--cmb-dev-ui'\]:\[\]/);
  assert.match(source,/authorize\(event\);return developmentReload\.request\(value\)/);
});
test('development preload counts every pending IPC, cleans up rejected requests, and strips native events',async()=>{
  const f=preload(['--cmb-dev-ui']);const request=f.api.cloud({action:'redeem',code:'fixture-only'});
  assert.equal(f.api.development.busy(),true);f.requests[0].reject(Error('result unknown'));await assert.rejects(request);
  assert.equal(f.api.development.busy(),false);
  let args;const remove=f.api.development.onChange((...value)=>args=value);
  const change={revision:1,files:['desktop/ui.js'],stylesOnly:false};f.emit({sender:'native'},change);
  assert.deepEqual(args,[change]);remove();assert.equal(f.listener,null);
});

function rendererFixture(state={reloaded:false,view:null,pending:null}){
  const events=new Map(),nodes=[],links=[],requests=[],clock=timers();let onChange,busy=false,active=null,modal=false,time=10000;
  const makeNode=()=>({setAttribute(){},remove(){this.removed=true;},closest(){return null;}});
  const doc={body:{append(node){nodes.push(node);}},hidden:false,createElement:makeNode,
    addEventListener(name,fn){events.set(name,fn);},removeEventListener(name){events.delete(name);},
    querySelector(){return modal?{}:null;},querySelectorAll(selector){return selector.startsWith('link')?links:doc.inputs;},inputs:[],get activeElement(){return active;}};
  const win={console:{warn(){}},addEventListener(){}};
  const api={state:async()=>state,busy:()=>busy,onChange(fn){onChange=fn;return ()=>onChange=null;},reload:async value=>{requests.push(value);return {accepted:true};}};
  const controller=create(api,{window:win,document:doc,setPoll:()=>1,clearPoll(){},now:()=>time});
  return {controller,doc,nodes,links,requests,change:value=>onChange(value),input(node){events.get('input')({target:node});},
    set busy(value){busy=value;},set active(value){active=value;},set modal(value){modal=value;},advance(){time+=1000;},makeNode,clock};
}
const rendererChange={revision:1,files:['desktop/shell/ui.js'],stylesOnly:false};
test('renderer restores only the current view and defers refresh while dirty, focused, modal or submitting',async()=>{
  const f=rendererFixture({reloaded:true,view,pending:rendererChange});let dirty=true,restored;
  f.controller.attach({dirty:()=>dirty,view:()=>view,restore:value=>restored=value});await f.controller.restore();
  assert.equal(f.controller.reloaded,true);assert.deepEqual(restored,view);
  await f.controller.flush();assert.equal(f.requests.length,0);dirty=false;
  f.busy=true;await f.controller.flush();f.busy=false;
  f.active={matches:()=>true};await f.controller.flush();f.active=null;
  f.modal=true;await f.controller.flush();f.modal=false;assert.equal(f.requests.length,0);
  await f.controller.flush();assert.deepEqual(f.requests,[{revision:1,view}]);
  f.controller.close();assert.equal(f.nodes[0].removed,true);
});
test('unmanaged input stays local and blocks reload until cleared or its dialog is closed',async()=>{
  const f=rendererFixture();await f.controller.initialize;f.change(rendererChange);
  const input={value:'unsaved-secret',closest:()=>null};f.doc.inputs=[input];f.input(input);f.advance();
  await f.controller.flush();assert.equal(f.requests.length,0);
  input.closest=()=>({});await f.controller.flush();assert.equal(f.requests.length,1);
  assert.equal(JSON.stringify(f.requests).includes('unsaved-secret'),false);
});
test('CSS swaps wait for a successful load and keep existing styles on error',async()=>{
  const f=rendererFixture();await f.controller.initialize;
  const link={href:'file:///fixture/ui.css',remove(){this.removed=true;},cloneNode(){return {remove(){this.removed=true;}};},after(next){this.next=next;}};f.links.push(link);
  f.change({revision:1,files:['desktop/ui.css'],stylesOnly:true});await Promise.resolve();
  assert.equal(link.removed,undefined);assert.match(link.next.href,/dev-ui=1/);link.next.onerror();assert.equal(link.removed,undefined);
  await new Promise(resolve=>setImmediate(resolve));
  f.change({revision:2,files:['desktop/ui.css'],stylesOnly:true});await Promise.resolve();link.next.onload();
  assert.equal(link.removed,true);assert.equal(f.requests.length,0);
});
test('renderer startup keeps lazy client discovery and only autostarts on the first developer launch',async()=>{
  const source=fs.readFileSync(path.join(root,'desktop/shell/renderer.js'),'utf8');
  const boot=source.split('\n').find(line=>line.startsWith('api.language().then('));
  for(const reloaded of [true,false]){
    let starts=0,scans=0;
    await vm.runInNewContext(boot,{api:{language:async()=> 'en'},applyLanguage(){},feedback(){},refresh:async()=>{},
      developmentReload:{reloaded,restore:async()=>{}},desktopConnections:{scan:async()=>scans++},
      snapshot:{preferences:{autoStart:true},runtime:{running:false,portOccupied:false}},$:()=>({click(){starts++;}})});
    assert.equal(scans,0);assert.equal(starts,reloaded?0:1);
  }
});
