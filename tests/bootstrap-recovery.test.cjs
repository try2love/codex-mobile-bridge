'use strict';
const test=require('node:test'),assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const root=path.resolve(__dirname,'..');
function retried(url,attempt){const value=new URL(url,'https://gateway.test');value.searchParams.set('bridge_retry','0-'+attempt);return value.href;}

function fixture({styles=[],scripts=[],deferred=false,shellSheet,preloadSupported=true,features=[],appearance=null,systemDark=false,connection}={}){
  let now=0,id=0,reloads=0;const timers=new Map(),events=new Map(),nodes=new Map(),requests=[],preloads=[],optionalRequests=[];
  function node(tag='div'){
    return {tagName:tag.toUpperCase(),hidden:false,textContent:'',style:{},dataset:{},open:false,relList:{supports:()=>preloadSupported},
      setAttribute(k,v){this[k]=v;},removeAttribute(){},remove(){this.removed=true;}};
  }
  for(const name of ['status','title','message','retry','details','details-label','diagnostics'])nodes.set('bootstrap-'+name,node());
  function parent(){return {replaceChild(next,old){next.parentNode=this;old.removed=true;requests.push(next);}};}
  if(shellSheet!==undefined)nodes.set('bridge-shell-style',Object.assign(node('link'),{href:'/style.css?v=hash',sheet:shellSheet,parentNode:parent()}));
  const placeholders=(urls,tag)=>urls.map(url=>Object.assign(node(tag),{getAttribute:()=>url,parentNode:parent()}));
  const css=placeholders(styles,'link'),js=placeholders(scripts,'script');
  const optional=features.map(({url,group})=>Object.assign(node(url.endsWith('.css')?'link':'script'),{dataset:{bridgeFeature:group},getAttribute:()=>url}));
  let domReady;
  const document={currentScript:{src:'/bootstrap.js'},documentElement:{dataset:{}},querySelector(){return null;},readyState:deferred?'loading':'complete',getElementById:id=>nodes.get(id),createElement:node,
    addEventListener(name,fn){if(name==='DOMContentLoaded'||name==='readystatechange')domReady=fn;},
    head:{appendChild(n){(n.bridgeOptionalResource?optionalRequests:preloads).push(n);}},querySelectorAll:selector=>selector==='[data-bridge-feature]'?optional:selector.startsWith('link')?[...css,...optional.filter(n=>n.tagName==='LINK')]:[...js,...optional.filter(n=>n.tagName==='SCRIPT')]};
  const window={matchMedia:()=>({matches:systemDark}),addEventListener(name,fn){if(!events.has(name))events.set(name,new Set());events.get(name).add(fn);},removeEventListener(name,fn){events.get(name)?.delete(fn);}};
  const dispatch=(name,event)=>{for(const fn of events.get(name)||[])fn(event);};
  const context=vm.createContext({window,document,navigator:{userAgent:'fixture-no-secrets',connection},location:{href:'https://gateway.test/',hash:'',search:'',reload(){reloads++;}},
    localStorage:{getItem(key){return key==='bridge-appearance'?JSON.stringify(appearance):null;}},AbortController,Promise,URL,fetch(){},Date:class extends Date{static now(){return now;}},
    setTimeout(fn,delay){timers.set(++id,{fn,at:now+delay});return id;},clearTimeout(id){timers.delete(id);}});
  vm.runInContext(fs.readFileSync(path.join(root,'web/shared/bootstrap.js'),'utf8'),context);
  async function flush(){for(let i=0;i<8;i++)await Promise.resolve();}
  async function tick(ms){const end=now+ms;await flush();while(true){const due=[...timers].filter(([,t])=>t.at<=end).sort((a,b)=>a[1].at-b[1].at)[0];if(!due)break;now=due[1].at;timers.delete(due[0]);due[1].fn();await flush();}now=end;await flush();}
  function finish(n,failed=false){const callback=n[failed?'onerror':'onload'];assert.equal(typeof callback,'function');if(failed)dispatch('error',{target:n});callback();}
  return {nodes,requests,preloads,optionalRequests,window,document,dispatch,tick,finish,flush,domReady:()=>{document.readyState='interactive';domReady();},
    eagerFailure(){dispatch('error',{target:{id:'bridge-clipboard-script',tagName:'SCRIPT',src:'/message-actions.js?v=hash',parentNode:parent()}});},get reloads(){return reloads;}};
}

test('the eager shell stylesheet recovers even though its error preceded bootstrap execution',()=>{
  const ui=fixture({shellSheet:{cssRules:[]},styles:['/feature.css'],scripts:['/app.js']});
  assert.equal(ui.requests[0].href,retried('/style.css?v=hash',2));assert.equal(ui.requests.length,1);
  ui.finish(ui.requests[0]);assert.equal(ui.requests[1].href,'/feature.css');ui.finish(ui.requests[1]);assert.equal(ui.requests[2].src,'/app.js');
});

test('an unreadable failed shell stylesheet retries instead of aborting startup',()=>{
  const sheet={get cssRules(){throw Object.assign(new Error('Cannot access rules'),{name:'SecurityError'});}};
  const ui=fixture({shellSheet:sheet,styles:['/feature.css'],scripts:['/app.js']});
  assert.deepEqual(ui.requests.map(n=>n.href),[retried('/style.css?v=hash',2)]);
  assert.doesNotMatch(ui.nodes.get('bootstrap-title').textContent,/尚未加载完成/);
  ui.finish(ui.requests[0]);ui.finish(ui.requests[1]);ui.finish(ui.requests[2]);ui.window.BridgeBootstrap.ready();
  assert.equal(ui.nodes.get('bootstrap-status').hidden,true);
  assert.equal(ui.reloads,0);
});

test('a missing shell stylesheet retries while a loaded sheet is reused',()=>{
  const missing=fixture({shellSheet:null,styles:['/feature.css']});
  assert.equal(missing.requests[0].href,retried('/style.css?v=hash',2));
  const loaded=fixture({shellSheet:{cssRules:[{}]},styles:['/feature.css']});
  assert.deepEqual(loaded.requests.map(n=>n.href),['/feature.css']);
});

test('an unreadable shell stylesheet keeps the initial request within the three-attempt limit',async()=>{
  const sheet={get cssRules(){throw Object.assign(new Error('Cannot access rules'),{name:'SecurityError'});}};
  const ui=fixture({shellSheet:sheet,scripts:['/app.js']});
  ui.finish(ui.requests[0],true);await ui.tick(1500);ui.finish(ui.requests[1],true);await ui.tick(60000);
  assert.deepEqual(ui.requests.map(n=>n.href),[retried('/style.css?v=hash',2),retried('/style.css?v=hash',3)]);
  assert.match(ui.nodes.get('bootstrap-title').textContent,/尚未加载完成/);
});

test('styles retain cascade order, stay bounded at four and all finish before scripts',()=>{
  const urls=['a','b','c','d','e'].map(x=>'/'+x+'.css'),ui=fixture({styles:urls,scripts:['/app.js']});
  assert.equal(ui.requests.length,4);ui.finish(ui.requests[3]);assert.equal(ui.requests.length,5);
  ui.finish(ui.requests[4]);ui.finish(ui.requests[2]);ui.finish(ui.requests[1]);assert.equal(ui.requests.length,5);
  ui.finish(ui.requests[0]);assert.deepEqual(ui.requests.slice(0,5).map(n=>n.href),urls);assert.equal(ui.requests[5].src,'/app.js');
});

test('failed eager clipboard script recovers before downstream execution without an idle load-event gap',()=>{
  const ui=fixture({deferred:true,scripts:['/app.js']});ui.domReady();ui.eagerFailure();assert.equal(ui.requests.length,1);
  ui.domReady();assert.equal(ui.requests.length,1);ui.finish(ui.requests[0],true);assert.equal(ui.requests.length,2);
  assert.equal(ui.requests[0].src,retried('/message-actions.js?v=hash',2));
  assert.equal(ui.requests[1].src,retried('/message-actions.js?v=hash',3));ui.finish(ui.requests[1]);assert.equal(ui.requests[2].src,'/app.js');
});

test('transient stylesheet failure recovers without showing the fatal page or changing its URL',async()=>{
  const ui=fixture({styles:['/a.css?v=hash'],scripts:['/a.js?v=hash']});
  ui.finish(ui.requests[0],true);
  assert.doesNotMatch(ui.nodes.get('bootstrap-title').textContent,/尚未加载完成/);
  assert.equal(ui.requests.length,1);await ui.tick(500);
  assert.equal(ui.requests[1].href,retried('/a.css?v=hash',2));ui.finish(ui.requests[1]);
  assert.equal(ui.requests[2].src,'/a.js?v=hash');ui.finish(ui.requests[2]);ui.window.BridgeBootstrap.ready();
  assert.equal(ui.nodes.get('bootstrap-status').hidden,true);assert.equal(ui.reloads,0);
});

test('script retries hold the execution barrier while all required downloads are discovered early',async()=>{
  const urls=['a','b','c','d','e'].map(x=>'/'+x+'.js?v=hash'),ui=fixture({scripts:urls});
  assert.equal(ui.requests.length,1);assert.deepEqual(ui.preloads.map(n=>n.href),urls);
  const failed=ui.requests[0];ui.finish(failed,true);await ui.tick(500);
  assert.equal(ui.requests.length,2);assert.equal(ui.requests[1].src,retried(urls[0],2));
  ui.finish(ui.requests[1]);assert.equal(ui.requests[2].src,urls[1]);
  for(let i=2;i<6;i++)ui.finish(ui.requests[i]);
  assert.deepEqual(ui.requests.map(n=>n.src),[urls[0],retried(urls[0],2),...urls.slice(1)]);
  assert.ok(ui.requests.every(n=>n.async===false));
});

test('browsers without preload still load and execute dependencies in order',()=>{
  const ui=fixture({scripts:['/first.js','/app.js'],preloadSupported:false});
  assert.equal(ui.preloads.length,0);assert.deepEqual(ui.requests.map(n=>n.src),['/first.js']);
  ui.finish(ui.requests[0]);assert.deepEqual(ui.requests.map(n=>n.src),['/first.js','/app.js']);
  ui.finish(ui.requests[1]);ui.window.BridgeBootstrap.ready();assert.equal(ui.nodes.get('bootstrap-status').hidden,true);
});

test('script preloads overlap pending styles and retain their content-version URLs',()=>{
  const urls=['/a.js?v=first','/b.js?v=second'],ui=fixture({styles:['/pending.css'],scripts:urls});
  assert.deepEqual(ui.preloads.map(n=>n.href),urls);
  assert.ok(ui.preloads.every(n=>n.rel==='preload'&&n.as==='script'));
  assert.equal(ui.requests.length,1);assert.equal(ui.requests[0].href,'/pending.css');
  ui.finish(ui.requests[0]);assert.equal(ui.requests[1].src,urls[0]);
});

test('explicit resource failures get distinct cache keys without changing path, origin or content version',async()=>{
  const url='/app.js?v=content-hash&lang=en#fixture',ui=fixture({scripts:[url]});
  assert.equal(ui.requests[0].src,url);assert.equal(ui.preloads[0].href,url);
  ui.finish(ui.requests[0],true);await ui.tick(500);
  assert.equal(ui.requests[1].src,retried(url,2));
  ui.finish(ui.requests[1],true);await ui.tick(1500);
  assert.equal(ui.requests[2].src,retried(url,3));
  for(const node of ui.requests){
    const value=new URL(node.src,'https://gateway.test');
    assert.equal(value.origin,'https://gateway.test');assert.equal(value.pathname,'/app.js');
    assert.equal(value.searchParams.get('v'),'content-hash');assert.equal(value.searchParams.get('lang'),'en');assert.equal(value.hash,'#fixture');
  }
  ui.finish(ui.requests[2],true);await ui.tick(60000);assert.equal(ui.requests.length,3);
  assert.match(ui.nodes.get('bootstrap-diagnostics').textContent,/3 \/ 3/);
  assert.doesNotMatch(ui.nodes.get('bootstrap-diagnostics').textContent,/content-hash|lang=en|#fixture|bridge_retry/);
});

test('a blocked proxy-injected analytics script does not stop the workspace',()=>{
  const ui=fixture({deferred:true,scripts:['/app.js']});
  ui.dispatch('error',{target:{tagName:'SCRIPT',src:'https://static.cloudflareinsights.com/beacon.min.js'}});
  assert.doesNotMatch(ui.nodes.get('bootstrap-title').textContent,/尚未加载完成/);
  ui.domReady();ui.domReady();assert.equal(ui.requests.length,1);ui.finish(ui.requests[0]);ui.window.BridgeBootstrap.ready();
  assert.equal(ui.nodes.get('bootstrap-status').hidden,true);
});

test('an opaque window error before DOMContentLoaded does not cancel startup at zero seconds',()=>{
  const ui=fixture({deferred:true,scripts:['/app.js']});ui.domReady();
  ui.dispatch('error',{target:ui.window,message:'Script error.',filename:'',lineno:0,error:null});
  assert.doesNotMatch(ui.nodes.get('bootstrap-title').textContent,/尚未加载完成/);
  assert.equal(ui.requests.length,0);
  ui.domReady();assert.equal(ui.requests.length,1);ui.finish(ui.requests[0]);ui.window.BridgeBootstrap.ready();
  assert.equal(ui.nodes.get('bootstrap-status').hidden,true);
});

test('foreign, injected and optional runtime errors cannot stop required scripts',()=>{
  for(const filename of ['https://analytics.test/app.js','safari-extension://fixture/injected.js','https://gateway.test/injected.js','https://gateway.test/optional.js']){
    const ui=fixture({scripts:['/app.js'],features:[{url:'/optional.js',group:'files'}]});
    ui.dispatch('error',{target:ui.window,filename,error:{name:'TypeError',stack:'TypeError\n    at https://gateway.test/app.js:2:1'}});
    ui.finish(ui.requests[0]);ui.window.BridgeBootstrap.ready();
    assert.equal(ui.nodes.get('bootstrap-status').hidden,true,filename);
  }
});

test('unknown promise rejections remain nonfatal and never postpone the startup deadline',async()=>{
  for(const reason of [null,'private-rejection',{name:'Error',message:'private-rejection'},{name:'TypeError',stack:'TypeError: private-message\n    at https://other.test/app.js?private-query:8:2'}]){
    const ui=fixture({scripts:['/app.js']});
    ui.dispatch('unhandledrejection',{reason});
    assert.doesNotMatch(ui.nodes.get('bootstrap-title').textContent,/尚未加载完成/);
    await ui.tick(29000);ui.dispatch('unhandledrejection',{reason});await ui.tick(1000);
    assert.match(ui.nodes.get('bootstrap-title').textContent,/尚未加载完成/);
    assert.doesNotMatch(ui.nodes.get('bootstrap-message').textContent,/页面组件/);
    assert.doesNotMatch(ui.nodes.get('bootstrap-diagnostics').textContent,/private-|other\.test/);
  }
});

test('required script rejections retain a terminal barrier with WebKit and V8 source metadata',()=>{
  for(const extra of [
    {sourceURL:'https://gateway.test/a.js?private-query',line:17},
    {fileName:'https://gateway.test/a.js?private-query'},
    {stack:'load@https://gateway.test/a.js?private-query:17:3\nglobal code@https://gateway.test/other.js:1:1'},
    {stack:'TypeError: private-message\n    at load (https://gateway.test/a.js?private-query:17:3)\n    at https://gateway.test/other.js:1:1'}
  ]){
    const ui=fixture({scripts:['/a.js','/b.js']});
    ui.dispatch('unhandledrejection',{reason:{name:'TypeError',message:'private-message',...extra}});
    ui.finish(ui.requests[0]);ui.window.BridgeBootstrap.ready();
    assert.equal(ui.requests.length,1);assert.equal(ui.nodes.get('bootstrap-status').hidden,false);
    assert.match(ui.nodes.get('bootstrap-diagnostics').textContent,/TypeError @ \/a\.js/);
    assert.doesNotMatch(ui.nodes.get('bootstrap-diagnostics').textContent,/private-|gateway\.test/);
  }
});

test('the bootstrap itself remains a required runtime source',()=>{
  const ui=fixture({scripts:['/app.js']});
  ui.dispatch('error',{target:ui.window,filename:'https://gateway.test/bootstrap.js',error:{name:'TypeError'}});
  ui.finish(ui.requests[0]);ui.window.BridgeBootstrap.ready();
  assert.equal(ui.nodes.get('bootstrap-status').hidden,false);
  assert.match(ui.nodes.get('bootstrap-diagnostics').textContent,/TypeError @ \/bootstrap\.js/);
});

test('permanent script failure stops after three attempts and keeps diagnostics collapsed',async()=>{
  const ui=fixture({scripts:['/a.js?v=secret-query','/b.js']});
  ui.finish(ui.requests[0],true);await ui.tick(500);ui.finish(ui.requests[1],true);await ui.tick(1500);ui.finish(ui.requests[2],true);await ui.tick(60000);
  assert.equal(ui.requests.length,3);assert.match(ui.nodes.get('bootstrap-title').textContent,/尚未加载完成/);
  assert.doesNotMatch(ui.nodes.get('bootstrap-message').textContent,/resource|fixture-no-secrets|启动诊断/);
  assert.match(ui.nodes.get('bootstrap-diagnostics').textContent,/resource @ \/a.js/);
  assert.doesNotMatch(ui.nodes.get('bootstrap-diagnostics').textContent,/secret-query|gateway.test/);
  assert.equal(ui.nodes.get('bootstrap-details').open,false);
});

test('a hanging script is not duplicated at the soft deadline and cannot resume after the hard deadline',async()=>{
  const ui=fixture({scripts:['/a.js','/b.js']}),late=ui.requests[0].onload;
  await ui.tick(12000);assert.equal(ui.requests.length,1);assert.doesNotMatch(ui.nodes.get('bootstrap-title').textContent,/尚未加载完成/);
  await ui.tick(18000);assert.match(ui.nodes.get('bootstrap-title').textContent,/尚未加载完成/);
  late();assert.equal(ui.requests.length,1);
});

test('runtime exceptions are terminal even when the script subsequently fires load',async()=>{
  const ui=fixture({scripts:['/a.js','/b.js']});
  ui.dispatch('error',{target:ui.window,error:{name:'ReferenceError'},filename:'https://gateway.test/a.js?secret'});
  ui.finish(ui.requests[0]);await ui.tick(60000);
  assert.equal(ui.requests.length,1);assert.match(ui.nodes.get('bootstrap-diagnostics').textContent,/ReferenceError/);
});

test('ready waits for the final script and cancels all startup feedback',async()=>{
  const ui=fixture({scripts:['/app.js','/presentation.js']});ui.window.BridgeBootstrap.ready();
  assert.equal(ui.nodes.get('bootstrap-status').hidden,false);ui.finish(ui.requests[0]);ui.finish(ui.requests[1]);
  assert.equal(ui.nodes.get('bootstrap-status').hidden,true);await ui.tick(60000);assert.equal(ui.nodes.get('bootstrap-status').hidden,true);
});

test('startup auth retries only retryable reads and aborts timed-out attempts',async()=>{
  const ui=fixture();ui.window.BridgeBootstrap.waitingForAuth();let calls=0,firstSignal;
  const result=ui.window.BridgeBootstrap.readAuth(signal=>{calls++;if(calls===1){firstSignal=signal;return new Promise(()=>{});}return Promise.resolve({authenticated:false});});
  await ui.tick(8000);assert.equal(firstSignal.aborted,true);await ui.tick(500);
  assert.deepEqual(await result,{authenticated:false});assert.equal(calls,2);
});

test('auth HTTP failures are bounded while denial and programming errors are not retried',async()=>{
  for(const error of [Object.assign(new Error('denied'),{status:403}),new SyntaxError('bad JSON')]){
    const ui=fixture();let calls=0;const result=ui.window.BridgeBootstrap.readAuth(()=>{calls++;return Promise.reject(error);});
    await assert.rejects(result);await ui.tick(60000);assert.equal(calls,1);
  }
  const ui=fixture();let calls=0;const result=ui.window.BridgeBootstrap.readAuth(()=>{calls++;return Promise.reject(Object.assign(new Error('upstream'),{status:503}));});
  const checked=assert.rejects(result);await ui.tick(2000);await checked;assert.equal(calls,3);
});

test('pagehide cancels pending recovery and ignores a late auth response',async()=>{
  const ui=fixture();let resolve,signal;const result=ui.window.BridgeBootstrap.readAuth(s=>{signal=s;return new Promise(r=>{resolve=r;});});
  const checked=assert.rejects(result,error=>error.startupCancelled===true);await ui.flush();ui.dispatch('pagehide',{});resolve({authenticated:true});await checked;
  assert.equal(signal.aborted,true);await ui.tick(60000);assert.equal(ui.reloads,0);
});

test('back-forward cache suspension retains the current startup instead of abandoning it',async()=>{
  const ui=fixture({scripts:['/a.js']});ui.dispatch('pagehide',{persisted:true});ui.finish(ui.requests[0],true);
  await ui.tick(500);assert.equal(ui.requests.length,2);ui.finish(ui.requests[1]);ui.window.BridgeBootstrap.ready();
  assert.equal(ui.nodes.get('bootstrap-status').hidden,true);
});

test('a late timed-out auth result cannot replace the successful retry',async()=>{
  const ui=fixture();let late,calls=0;const result=ui.window.BridgeBootstrap.readAuth(()=>++calls===1?new Promise(resolve=>{late=resolve;}):Promise.resolve({authenticated:false}));
  await ui.tick(8500);assert.deepEqual(await result,{authenticated:false});late({authenticated:true});await ui.flush();assert.equal(calls,2);
});


test('optional math, terminal and file failures cannot prevent the list from starting',async()=>{
  const ui=fixture({scripts:['/app.js'],features:[{url:'/katex.js',group:'math'},{url:'/xterm.css',group:'terminal'},{url:'/source.js',group:'files'}]});
  assert.deepEqual(ui.requests.map(n=>n.src),['/app.js']);
  assert.deepEqual(ui.preloads.map(n=>n.href),['/app.js']);
  assert.equal(ui.optionalRequests.length,0);
  // Failure can also occur before auth completes, without triggering the global fatal handler.
  const load=ui.window.BridgeFeatures.load('math');await ui.flush();
  ui.finish(ui.optionalRequests[0],true);await assert.rejects(load);
  ui.finish(ui.requests[0]);ui.window.BridgeBootstrap.ready();
  assert.equal(ui.nodes.get('bootstrap-status').hidden,true);
  assert.equal(ui.optionalRequests.length,1);
});

test('lazy components share in-flight work, preserve order and reuse successful downloads after failure',async()=>{
  const ui=fixture({features:[{url:'/xterm.css',group:'terminal'},{url:'/xterm.js',group:'terminal'},{url:'/fit.js',group:'terminal'}]});
  const one=ui.window.BridgeFeatures.load('terminal'),two=ui.window.BridgeFeatures.load('terminal');
  assert.deepEqual(ui.preloads.filter(node=>node.rel==='preload').map(node=>node.href),['/xterm.js','/fit.js']);
  assert.equal(ui.optionalRequests.length,1);ui.finish(ui.optionalRequests[0]);await ui.flush();
  assert.equal(ui.optionalRequests[1].src,'/xterm.js');ui.finish(ui.optionalRequests[1]);await ui.flush();
  assert.equal(ui.optionalRequests[2].src,'/fit.js');ui.finish(ui.optionalRequests[2],true);
  await Promise.all([assert.rejects(one),assert.rejects(two)]);
  const retry=ui.window.BridgeFeatures.load('terminal');await ui.flush();
  assert.equal(ui.optionalRequests.length,4);assert.equal(ui.optionalRequests[3].src,'/fit.js');
  ui.finish(ui.optionalRequests[3]);await retry;await ui.window.BridgeFeatures.load('terminal');
  assert.equal(ui.optionalRequests.length,4);
});

test('a slow lazy script is not duplicated when the caller retries after its waiting limit',async()=>{
  const ui=fixture({features:[{url:'/source.js',group:'files'}]});
  const first=ui.window.BridgeFeatures.load('files'),rejected=assert.rejects(first);await ui.tick(45000);await rejected;
  const retry=ui.window.BridgeFeatures.load('files');await ui.flush();assert.equal(ui.optionalRequests.length,1);
  ui.finish(ui.optionalRequests[0]);await retry;
});

test('weak networks with successful progress can take longer than 30 seconds in total',async()=>{
  const ui=fixture({scripts:['/one.js','/two.js','/app.js']});
  for(let i=0;i<3;i++){await ui.tick(11000);ui.finish(ui.requests[i]);}
  ui.window.BridgeBootstrap.ready();assert.equal(ui.nodes.get('bootstrap-status').hidden,true);
});

test('production markup excludes optional renderers from critical loading but retains versionable URLs',()=>{
  const html=fs.readFileSync(path.join(root,'web/index.html'),'utf8');
  for(const [url,group] of [['/vendor/katex/katex.min.js','math'],['/vendor/katex/katex.min.css','math'],['/vendor/xterm/xterm.js','terminal'],['/terminal-panel.js','terminal'],['/source-view.js','files'],['/pdf-preview.js','files']]){
    const tag=html.match(new RegExp('<(?:script|link)[^>]*data-bridge-(?:src|href)="'+url.replaceAll('.','\\.')+'(?:\\?[^"]*)?"[^>]*>'))?.[0];
    assert.ok(tag,url);assert.ok(tag.includes('data-bridge-feature="'+group+'"'));
  }
});


test('saved and system palettes apply in head before startup DOM and resource requests',()=>{
  for(const [appearance,systemDark,expected] of [[null,true,'dark'],[{theme:'light'},true,'light'],[{theme:'dark'},false,'dark'],[{theme:'invalid'},true,'dark']]){
    const ui=fixture({deferred:true,appearance,systemDark,scripts:['/app.js']});
    assert.equal(ui.document.documentElement.dataset.theme,expected);assert.equal(ui.requests.length,0);
    ui.domReady();assert.equal(ui.requests.length,0);ui.domReady();assert.equal(ui.requests.length,1);
  }
});

test('chat prefetch waits for a list paint and skips constrained connections',async()=>{
  for(const connection of [{saveData:true},{effectiveType:'2g'},{effectiveType:'slow-2g'},{downlink:.5}]){
    const ui=fixture({connection,features:[{url:'/markdown.js',group:'chat'}]});
    ui.window.BridgeFeatures.prefetchChat();await ui.tick(2000);assert.equal(ui.optionalRequests.length,0);
  }
  const ui=fixture({features:[{url:'/markdown.js',group:'chat'}]});
  ui.window.BridgeFeatures.prefetchChat();ui.window.BridgeFeatures.prefetchChat();await ui.tick(1499);assert.equal(ui.optionalRequests.length,0);
  await ui.tick(1);assert.equal(ui.optionalRequests.length,1);assert.equal(ui.optionalRequests[0].fetchPriority,'low');
  const demand=ui.window.BridgeFeatures.load('chat');assert.equal(ui.optionalRequests[0].fetchPriority,'auto');ui.finish(ui.optionalRequests[0]);await demand;await ui.flush();
});

test('startup retains simple waiting feedback without skeleton markup or layout overrides',()=>{
  const html=fs.readFileSync(path.join(root,'web/index.html'),'utf8'),css=fs.readFileSync(path.join(root,'web/shell/style.css'),'utf8');
  assert.doesNotMatch(html+css,/bootstrap-list-placeholder/);assert.match(css,/bootstrap-reveal 0s \.4s/);
  assert.doesNotMatch(css,/bootstrap-status:not\(\[data-state="failed"\]\)\{visibility:visible/);
});
