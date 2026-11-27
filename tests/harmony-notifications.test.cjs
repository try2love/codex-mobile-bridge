'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),crypto=require('node:crypto');
const {stripTypeScriptTypes}=require('node:module');
function fixture(){
 const saved=new Map(),posted=[],requests=[],timers=new Map();let serial=0,cookie='session=fixture',auth={authenticated:true,sessionSync:{version:1,gatewayId:'a'.repeat(32),identity:'b'.repeat(64)}},reply={enabled:true,streamId:'fixture-stream',cursor:10,events:[],longPoll:60,revision:'c'.repeat(32)},permission=0;
 const context=vm.createContext({clearTimeout:id=>timers.delete(id),setTimeout:callback=>{timers.set(++serial,callback);return serial;},util:{TextEncoder:class{encodeInto(value){return new TextEncoder().encode(value);}}},Digest:{sha256:async data=>crypto.createHash('sha256').update(data).digest('hex')},
  preferences:{getPreferences:async()=>({getSync:(key,fallback)=>saved.get(key)??fallback,putSync:(key,value)=>saved.set(key,value),flush:async()=>{}})},
  DeviceBinding:{cookies:()=>cookie},GatewayHttp:class{cancel(){}async json(base,path){requests.push({base,path});return path==='/api/auth'?auth:await reply;}},
  notificationManager:{SlotType:{SOCIAL_COMMUNICATION:1},ContentType:{NOTIFICATION_CONTENT_BASIC_TEXT:0},requestEnableNotification:async()=>{permission++;},publish:async request=>posted.push(request)},
  bundleManager:{BundleFlag:{GET_BUNDLE_INFO_DEFAULT:0},getBundleInfoForSelfSync:()=>({name:'io.github.fixture'})},wantAgent:{getWantAgent:async value=>value,OperationType:{START_ABILITY:0},WantAgentFlags:{UPDATE_PRESENT_FLAG:0}}
 });
 const source=fs.readFileSync('mobile/harmony/entry/src/main/ets/services/TaskNotifications.ets','utf8').replace(/^import .*;\r?\n/gm,'').replace('export class','class');
 vm.runInContext(stripTypeScriptTypes(source)+';globalThis.TaskNotifications=TaskNotifications;',context);
 const notifications=new context.TaskNotifications(),flush=()=>new Promise(setImmediate);
 return {notifications,saved,posted,requests,timers,flush,permission:()=>permission,cookie:value=>cookie=value,auth:value=>auth=value,reply:value=>reply=value,
  configure:()=>notifications.configure({gateway:'a'.repeat(32),identity:'b'.repeat(64)},'https://gateway.example',cookie),
  async tick(){const [id,callback]=timers.entries().next().value||[];assert(callback);timers.delete(id);callback();await flush();}};
}
test('Harmony foreground notifications baseline old events, resume incrementally and retain only binding hashes',async()=>{
 const f=fixture();await f.notifications.init({});f.configure();assert.equal(f.requests.length,0);await f.notifications.enable(true);await f.flush();
 assert.equal(f.permission(),1);assert.equal(f.posted.length,0);assert.match(f.requests[0].path,/api\/auth/);
 f.reply({enabled:true,streamId:'fixture-stream',cursor:11,events:[{sequence:11,title:'Completed',body:'Result ready',unread:true}],longPoll:60,revision:'c'.repeat(32)});
 await f.tick();assert.equal(f.posted.length,1);assert.match(f.requests.at(-1).path,/wait=60/);assert.equal(f.posted[0].appMessageId.length,64);assert.equal(f.posted[0].isAlertOnce,true);
 assert.equal([...f.saved.values()].some(value=>String(value).includes('session=fixture')),false);
 f.notifications.visibility(false);assert.equal(f.timers.size,0);const count=f.requests.length;await f.flush();assert.equal(f.requests.length,count);
 f.notifications.visibility(true);await f.flush();assert.equal(f.posted.length,1);f.notifications.detach();
});
test('Harmony drops late notification reads after logout and refuses a replaced gateway identity',async()=>{
 const f=fixture();await f.notifications.init({});f.configure();await f.notifications.enable(true);await f.flush();
 let resolve;f.reply(new Promise(done=>resolve=done));const tick=f.tick();await f.flush();f.cookie('');f.notifications.detach();
 resolve({enabled:true,streamId:'fixture-stream',cursor:11,events:[{sequence:11,title:'Late'}]});await tick;assert.equal(f.posted.length,0);assert.equal(f.timers.size,0);
 f.cookie('session=other');f.auth({authenticated:true,sessionSync:{version:1,gatewayId:'d'.repeat(32),identity:'b'.repeat(64)}});f.configure();await f.flush();
 assert.equal(f.requests.at(-1).path,'/api/auth');assert.equal(f.timers.size,0);f.notifications.detach();
});
