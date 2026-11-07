const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(require.resolve('../shared/web/channel-link.js'), 'utf8');

const lan='http://192.168.1.10:8788',pub='https://test.example',other='https://temporary.example';
function fixture(menu={current:pub,rows:[{url:pub}],mode:'auto',localNetwork:false}, session='') {
  let now = 0;
  const calls = [], timers = new Map();
  const window = new EventTarget();
  const document = new EventTarget();
  document.documentElement = {classList:{toggle(){}}};
  let reply=async()=>new Response('{}');window.fetch=(...args)=>reply(...args);
  window.prompt = (_, action) => {if(action==='read')return JSON.stringify(menu);calls.push(action); return action.startsWith('verify:') ? 'started' : 'ok';};
  let serial = 0;
  const context={window, document, location:new URL(menu.current),
    performance:{now:()=>now}, setTimeout:(fn,ms)=>{timers.set(++serial,{fn,at:now+ms});return serial;},
    clearTimeout:id=>timers.delete(id), csrf:session, URL, Request, Response, Headers, Event, DOMException, AbortController, TextDecoder};
  vm.runInNewContext(source,context);
  function advance(ms) {
    now += ms;
    for (const [id, t] of timers) if (now>=t.at) {timers.delete(id);t.fn();}
  }
  return {link:window.BridgeChannelLink, window, document, calls, advance, respond:fn=>{reply=fn;},session:value=>{context.csrf=value;}};
}

test('network changes verify only the current route and debounce an unconfirmed failure', () => {
  const {link,calls,advance} = fixture();
  link.check();link.check();link.result(1,true);
  link.check();link.result(2,false);link.check();
  assert.deepEqual(calls,['verify:1','verify:2']);
  advance(1200);link.result(3,true);
  assert.equal(link.down,false);
  assert.deepEqual(calls,['verify:1','verify:2','verify:3']);
});

test('confirmed outage opens one incident, backs off, and ignores late probe completions', () => {
  const {link,calls,advance} = fixture();
  link.check();link.result(1,false);advance(1200);link.result(2,false);
  assert.equal(link.down,true);
  link.result(1,true);assert.equal(link.down,true);
  advance(6000);link.result(3,false);
  assert.deepEqual(calls.filter(v=>!v.startsWith('verify:')),['unreachable']);
  advance(12000);link.result(4,true);
  assert.equal(link.down,false);
  advance(60000);
  assert.equal(calls.at(-1),'verify:4');
});

test('on a healthy public route only an enabled LAN is considered on Wi-Fi; manual mode stays put',()=>{
  const menu={current:pub,rows:[{url:other},{url:lan},{url:pub}],mode:'auto',localNetwork:false};
  const {link,calls}=fixture(menu);
  link.check();link.result(1,true);
  menu.localNetwork=true;link.check('network');link.result(2,true);
  link.check('tick');link.result(3,true);
  menu.mode='manual';link.check('network');link.result(4,true);
  menu.mode='auto';menu.rows=menu.rows.filter(x=>x.url!==lan);
  link.check('network');link.result(5,true);
  assert.deepEqual(calls.filter(v=>!v.startsWith('verify:')),['prefer:'+JSON.stringify([lan])]);
});

test('on confirmed loss only a LAN can fail over to another public address',()=>{
  for(const [current,localNetwork,expected] of [[lan,false,[other,pub]],[pub,false,[]],[pub,true,[lan]]]){
    const {link,calls,advance}=fixture({current,localNetwork,mode:'auto',rows:[other,lan,pub].map(url=>({url}))});
    link.check();link.result(1,false);advance(1200);link.result(2,false);
    assert.equal(calls.at(-1),expected.length?'outage:'+JSON.stringify(expected):'unreachable');
  }
});

test('successful foreground traffic suppresses a redundant timer proof but never a network-change check',async()=>{
  const {link,window,calls,advance}=fixture();link.start();link.check();link.result(1,true);
  advance(29000);await window.fetch(pub+'/api/sessions');link.check('tick');
  assert.deepEqual(calls,['verify:1']);link.check('network');
  assert.equal(calls.at(-1),'verify:2');link.result(2,true);
  advance(90000);await window.fetch(pub+'/api/sessions');link.check('tick');
  assert.equal(calls.at(-1),'verify:3');
});


test('failed native proof cannot mark a matching authenticated current channel unreachable',async()=>{
  const f=fixture(undefined,'paired-csrf');
  const requests=[];
  f.respond(async(url,options)=>{requests.push([url,options]);f.advance(45);return Response.json({authenticated:true,trustedDevice:true,csrf:'paired-csrf'});});
  f.link.start();f.link.check();await f.link.result(1,false);
  f.advance(1200);f.link.check();await f.link.result(2,false);
  assert.equal(f.link.down,false);
  assert.equal(f.link.latency,45);
  assert.equal(requests.length,2);
  assert.equal(requests[0][0],pub+'/api/auth');
  assert.equal(requests[0][1].credentials,'same-origin');
  assert.equal(requests[0][1].redirect,'error');
  assert.equal(requests[0][1].cache,'no-store');
  assert.ok(!f.calls.includes('unreachable'));
});

test('manual current-channel timing uses authenticated transport and clears only a verified recovery',async()=>{
  const f=fixture(undefined,'paired-csrf');
  f.link.start();f.link.check();await f.link.result(1,false);f.advance(1200);await f.link.result(2,false);
  assert.equal(f.link.down,true);
  f.respond(async()=>{f.advance(72);return Response.json({authenticated:true,trustedDevice:true,csrf:'paired-csrf'});});
  assert.equal(await f.link.measure(),true);
  assert.equal(f.link.down,false);assert.equal(f.link.latency,72);
});

test('a successful HTTP status with a different login never proves or measures the current binding',async()=>{
  for(const auth of [{authenticated:false},{authenticated:true,trustedDevice:false,csrf:'paired-csrf'},
    {authenticated:true,trustedDevice:true,csrf:'different-csrf'}]){
    const f=fixture(undefined,'paired-csrf');f.link.start();f.respond(async()=>Response.json(auth));
    f.link.check();await f.link.result(1,false);f.advance(1200);await f.link.result(2,false);
    assert.equal(f.link.down,true);assert.equal(f.link.latency,null);
  }
});

test('a probe finishing after the page is backgrounded cannot emit a disconnect warning',async()=>{
  const f=fixture(undefined,'paired-csrf');f.link.start();
  f.link.check();await f.link.result(1,false);f.advance(1200);
  f.document.hidden=true;await f.link.result(2,false);
  assert.equal(f.link.down,false);assert.ok(!f.calls.includes('unreachable'));
});

test('concurrent refreshes share one bounded read and discard an obsolete login response',async()=>{
  const f=fixture(undefined,'paired-csrf');let requests=0,resolve;
  f.respond(()=>{requests++;return new Promise(done=>{resolve=done;});});
  const first=f.link.measure(),second=f.link.measure();assert.equal(first,second);assert.equal(requests,1);
  f.session('new-csrf');resolve(Response.json({authenticated:true,trustedDevice:true,csrf:'paired-csrf'}));
  assert.equal(await first,false);assert.equal(f.link.latency,null);
  f.respond((url,options)=>new Promise((_,reject)=>options.signal.addEventListener('abort',()=>reject(new DOMException('timeout','AbortError')))));
  const timed=f.link.measure();f.advance(5000);assert.equal(await timed,false);assert.equal(f.link.latency,null);
});

test('a probe whose headers arrive but body stalls is cancelled within its deadline',async()=>{
  const f=fixture(undefined,'paired-csrf');let cancelled=false;
  f.respond(async()=>new Response(new ReadableStream({cancel(){cancelled=true;}})));
  const pending=f.link.measure();
  await new Promise(resolve=>setImmediate(resolve));f.advance(5000);
  assert.equal(await pending,false);assert.equal(cancelled,true);assert.equal(f.link.latency,null);
});
