const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

function fixture(kind) {
  const listeners = new Set(), timers = new Map(), requests = [];
  let next = 0, identity = 0;
  const node = () => ({append(){}, setAttribute(){}, classList:{add(){},toggle(){}}, textContent:''});
  const document = {hidden:false, documentElement:{classList:{contains:()=>false}},
    addEventListener:(type, fn)=>listeners.add(fn), removeEventListener:(type,fn)=>listeners.delete(fn)};
  const context = vm.createContext({document, AbortController, BridgeI18n:{t:x=>x}, uuid:()=> 'terminal-'+ ++identity,
    sessionStorage:{getItem:()=>null,setItem(){},removeItem(){}},
    setTimeout:(fn,ms)=>{timers.set(++next,{fn,ms});return next;},clearTimeout:id=>timers.delete(id)});
  const name = kind === 'pty' ? 'TerminalPanel' : 'CommandTerminalPanel';
  vm.runInContext(fs.readFileSync(path.join(__dirname,'../web/hosts/environment.js'),'utf8'),context);
  vm.runInContext(fs.readFileSync(path.join(__dirname,'../web/layouts/viewport.js'),'utf8'),context);
  vm.runInContext(fs.readFileSync(path.join(__dirname,'../web/features/workspace/',kind==='pty'?'terminal-panel.js':'command-terminal-panel.js'),'utf8')+`;globalThis.Panel=${name};`,context);
  context.Panel.prototype.load = () => {};
  const workbench = {paint(){},node,button:node,isVisible:()=>true,endpoint:()=>'/terminal?',
    request(url,body,signal){
      if(body) {requests.push({url,body});return Promise.resolve({});}
      let resolve,reject;
      const promise = new Promise((yes,no)=>{resolve=yes;reject=no;});
      signal?.addEventListener('abort',()=>reject(new DOMException('Aborted','AbortError')));
      requests.push({url,signal,resolve,reject});return promise;
    }};
  const panel = new context.Panel(workbench,{key:'fixture'}, {body:node()});
  Object.assign(panel,{running:true,id:'terminal-id',cursor:17,status:node(),send:node(),reopen:node(),
    run:node(),stop:node(),term:{dispose(){}},applied:[]});
  panel.apply = value=>{panel.cursor=value.cursor;panel.running=value.running;panel.applied.push(value);};
  return {panel,requests,timers,listeners,
    visibility(hidden){document.hidden=hidden;for(const listener of listeners)listener();}};
}

test('command: stopping preserves final unread output using the existing cursor',async()=>{
  const f=fixture('command');
  f.panel.workbench.request=async(url,body)=>{
    assert.equal(body.action,'stop');assert.equal(body.after,17);
    return {cursor:22,running:false,output:'tail',exitCode:0};
  };
  await f.panel.cancel();
  assert.equal(f.panel.applied[0].output,'tail');assert.equal(f.panel.cursor,22);
  assert.equal(f.timers.size,0);f.panel.dispose();
});

test('pty: slow restart never polls the old shell and resumes after open',async()=>{
  const f=fixture('pty');let finishClose;
  Object.assign(f.panel.term,{reset(){},cols:80,rows:24});f.panel.retry={};
  const read=f.panel.poll();
  f.panel.workbench.request=(url,body)=>{
    assert.ok(body,'no read is allowed while restarting');
    if(body.action==='close')return new Promise(resolve=>finishClose=resolve);
    assert.equal(body.action,'open');return Promise.resolve({cursor:0,running:true,output:'fresh'});
  };
  const restart=f.panel.restart();await read;
  assert.equal(f.timers.size,0);await f.panel.poll();assert.equal(f.requests.length,1);
  finishClose({});await restart;
  assert.notEqual(f.panel.id,'terminal-id');assert.equal(f.panel.cursor,0);
  assert.equal(f.panel.applied.length,1);assert.equal(f.timers.size,1);
  f.panel.workbench.request=()=>Promise.resolve({});f.panel.dispose();
});
const settle = () => new Promise(resolve=>setImmediate(resolve));

for (const kind of ['pty','command']) {
  test(`${kind}: background stops timers, aborts read, retains shell and cursor`,async()=>{
    const f=fixture(kind);f.panel.schedule();
    assert.equal([...f.timers.values()][0].ms,kind==='pty'?150:900);
    const pending=f.panel.poll();assert.equal(f.requests.length,1);
    f.visibility(true);await pending;
    assert.equal(f.requests[0].signal.aborted,true);
    assert.equal(f.timers.size,0);
    for(let i=0;i<10;i++) {await f.panel.poll();f.panel.schedule();}
    assert.equal(f.requests.length,1);assert.equal(f.panel.cursor,17);
    assert.equal(f.requests.some(x=>x.body),false);
    f.visibility(false);assert.equal(f.requests.length,2);
    assert.match(f.requests[1].url,/after=17$/);
    f.requests[1].resolve({running:true,cursor:23,output:'catch up',reset:true,truncated:true});
    await settle();assert.equal(f.panel.applied.length,1);assert.equal(f.panel.cursor,23);
    assert.equal(f.panel.applied[0].truncated,true);
    f.panel.dispose();assert.equal(f.listeners.size,0);assert.equal(f.timers.size,0);
  });
  test(`${kind}: rapid foreground/background transition resumes exactly one read`,async()=>{
    const f=fixture(kind);const pending=f.panel.poll();
    f.visibility(true);f.visibility(false);f.visibility(false);
    await pending;await settle();
    assert.equal(f.requests.length,2);
    f.requests[1].resolve({running:true,cursor:19,output:'new'});await settle();
    assert.equal(f.panel.applied.length,1);
    assert.equal(f.timers.size,1);f.panel.dispose();
  });
  test(`${kind}: disposal cancels pending output and removes foreground listener`,async()=>{
    const f=fixture(kind);const pending=f.panel.poll();f.panel.dispose();await pending;
    f.visibility(false);assert.equal(f.panel.applied.length,0);
    assert.equal(f.requests.filter(x=>!x.body).length,1);
    assert.equal(f.listeners.size,0);assert.equal(f.timers.size,0);
  });
  test(`${kind}: ordinary errors retry, terminal disappearance stops`,async()=>{
    const f=fixture(kind);let pending=f.panel.poll();
    f.requests[0].reject(new Error('offline'));await pending;assert.equal(f.timers.size,1);
    pending=f.panel.poll();f.requests[1].reject(Object.assign(new Error('gone'),{status:404}));await pending;
    assert.equal(f.timers.size,0);assert.equal(f.panel.running,false);f.panel.dispose();
  });
}
