const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(require.resolve('../shared/web/channel-link.js'), 'utf8');

const lan='http://192.168.1.10:8788',pub='https://test.example',other='https://temporary.example';
function fixture(menu={current:pub,rows:[{url:pub}],mode:'auto',localNetwork:false}) {
  let now = 0;
  const calls = [], timers = new Map();
  const window = new EventTarget();
  const document = new EventTarget();
  document.documentElement = {classList:{toggle(){}}};
  window.fetch = async () => new Response('{}');
  window.prompt = (_, action) => {if(action==='read')return JSON.stringify(menu);calls.push(action); return action.startsWith('verify:') ? 'started' : 'ok';};
  let serial = 0;
  vm.runInNewContext(source, {window, document, location:new URL(menu.current),
    performance:{now:()=>now}, setTimeout:(fn,ms)=>{timers.set(++serial,{fn,at:now+ms});return serial;},
    clearTimeout:id=>timers.delete(id), URL, Request, Response, Headers, Event, DOMException, AbortController});
  function advance(ms) {
    now += ms;
    for (const [id, t] of timers) if (now>=t.at) {timers.delete(id);t.fn();}
  }
  return {link:window.BridgeChannelLink, window, calls, advance};
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

