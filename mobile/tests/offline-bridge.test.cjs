'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const test = require('node:test');
const vm = require('node:vm');
const source = fs.readFileSync(new URL('../shared/web/mobile-offline.js', `file://${__filename}`), 'utf8');

function harness({android = false, frame = false, userAgent} = {}) {
  const listeners = new Map();
  const prompts = [];
  const window = {
    prompt(label, payload) {
      prompts.push([label, JSON.parse(payload)]);
      return 'queued';
    },
    addEventListener(name, handler) { listeners.set(name, handler); },
    removeEventListener(name) { listeners.delete(name); },
    dispatchEvent(event) { listeners.get(event.type)?.(event); }
  };
  window.top = frame ? {} : window;
  class CustomEvent {
    constructor(type, data) { this.type = type; this.detail = data.detail; }
  }
  const changes = [], foreground = [];
  listeners.set('codexbridge-native-offline-state', event => changes.push(event.detail));
  listeners.set('bridge-chat-foreground', event => foreground.push(event.detail.foreground));
  vm.runInNewContext(source, {
    window, CustomEvent, setTimeout, clearTimeout,
    document: {addEventListener(name, handler) { listeners.set(name, handler); }},
    localStorage: {getItem: () => '{"fontSize":24,"density":"compact"}'},
    navigator: {userAgent: userAgent || (android ? 'Mozilla/5.0 BridgeMobile/0.1-Android' : 'Mozilla/5.0 BridgeMobile/0.1-iOS')}
  });
  return {window, listeners, prompts, changes, foreground};
}

test('offline script stays out of subframes', () => {
  const {window} = harness({frame: true});
  assert.equal(window.CodexBridgeNativeOffline, undefined);
});

test('iOS offline bridge authenticates replies and publishes app foreground changes', async () => {
  const {window, prompts, changes, foreground} = harness();
  const bridge = window.CodexBridgeNativeOffline;
  const call = bridge.request({type: 'state'});
  assert.equal(prompts[0][0], 'codexbridge-offline:__BRIDGE_CLIPBOARD_TOKEN__');
  const id = prompts[0][1].requestId;
  bridge.receive('wrong-token', {id, ok: true, value: 'incorrect'});
  bridge.receive('__BRIDGE_CLIPBOARD_TOKEN__', {id, ok: true, value: 'correct'});
  assert.equal(await call, 'correct');
  bridge.receive('__BRIDGE_CLIPBOARD_TOKEN__', {state: {foreground: true, allowPrefetch: false}});
  assert.deepEqual(foreground, [false, true]);
  assert.equal(changes[1].allowPrefetch, false);
  assert.equal(window.BridgeChatForeground, true);
});

test('Android archive requests use the transferred main-frame port', async () => {
  const {window, listeners, prompts} = harness({android: true});
  await assert.rejects(window.CodexBridgeNativeOffline.request({type: 'state'}), /unavailable/);
  const sent = [];
  const port = {postMessage: value => sent.push(value)};
  listeners.get('message')({data: 'wrong-token', ports: [port]});
  assert.deepEqual(sent, []);
  listeners.get('message')({data: '__BRIDGE_CLIPBOARD_TOKEN__', ports: [port]});
  assert.equal(sent.shift(), 'ready');
  const reply = window.CodexBridgeNativeOffline.request({type: 'read', sessionId: 'old'});
  const request = JSON.parse(sent[0]);
  assert.equal(request.sessionId, 'old');
  port.onmessage({data: JSON.stringify({id: request.requestId, ok: true, value: 'seed'})});
  assert.equal(await reply, 'seed');
  assert.deepEqual(prompts, []);
});

test('authenticated appearance transfers the saved snapshot and later edits', async () => {
  const {window, prompts, listeners} = harness();
  const bridge = window.CodexBridgeNativeOffline;
  const response = index => bridge.receive('__BRIDGE_CLIPBOARD_TOKEN__', {
    id:prompts[index][1].requestId, ok:true, value:{saved:true}
  });
  const login = bridge.request({type:'auth', status:'valid', gatewayId:'g', identity:'u'});
  assert.deepEqual(prompts[0][1].appearance, {fontSize:24,density:'compact'});
  response(0);
  await login;
  listeners.get('bridge-appearance-change')({detail:{fontSize:27}});
  assert.deepEqual(prompts[1][1], {
    type:'appearance',gatewayId:'g',identity:'u',appearance:{fontSize:27},requestId:'2'
  });
  response(1);
  const logout = bridge.request({type:'auth', status:'loggedOut'});
  response(2);
  await logout;
  listeners.get('bridge-appearance-change')({detail:{fontSize:28}});
  assert.equal(prompts.length,3);
});


for(const agent of [
 'Mozilla/5.0 BridgeMobile/0.1-Android',
 'Mozilla/5.0 BridgeMobile/2.2.2-preview.2026112307-Android BridgeModel/Google%20Pixel%207 BridgeOS/16 BridgeBuild/81'
])test('versioned Android native requests stay on the port and never open iOS prompt: '+agent,async()=>{
 const {window,listeners,prompts}=harness({userAgent:agent});
 const bridge=window.CodexBridgeNativeOffline;
 await assert.rejects(bridge.request({type:'state'}),/unavailable/);
 assert.equal(prompts.length,0,'Android must never fall through to the iOS prompt channel');
 const sent=[],port={postMessage:value=>sent.push(value)};
 listeners.get('message')({data:'__BRIDGE_CLIPBOARD_TOKEN__',ports:[port]});
 assert.equal(sent.shift(),'ready');
 for(const event of [{type:'auth',status:'valid',gatewayId:'g',identity:'u'},{type:'list'},{type:'session',sessionId:'saved'}]){
  const result=bridge.request(event),message=JSON.parse(sent.shift());
  assert.equal(message.type,event.type);
  port.onmessage({data:JSON.stringify({id:message.requestId,ok:true,value:{saved:true}})});
  assert.equal((await result).saved,true);
 }
 assert.equal(prompts.length,0);
});

test('versioned iOS retains its native prompt protocol',async()=>{
 const {window,prompts}=harness({userAgent:'Mozilla/5.0 BridgeMobile/2.2.2-preview.4-iOS BridgeModel/iPhone18%2C3 BridgeBuild/81'});
 const result=window.CodexBridgeNativeOffline.request({type:'state'});
 assert.equal(prompts.length,1);
 window.CodexBridgeNativeOffline.receive('__BRIDGE_CLIPBOARD_TOKEN__',{id:prompts[0][1].requestId,ok:true,value:{foreground:true}});
 assert.equal((await result).foreground,true);
});
