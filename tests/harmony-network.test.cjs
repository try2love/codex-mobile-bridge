'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const {stripTypeScriptTypes}=require('node:module');
test('Harmony native network policy rejects cellular auto-transfer and fences stale network callbacks',async()=>{
 let id=0,changed=0,handle=1,caps={networkCap:[1],bearerTypes:[1]},pending=null;
 const monitors=[],context=vm.createContext({util:{generateRandomUUID:()=>String(++id)},connection:{
  NetCap:{NET_CAPABILITY_NOT_METERED:1},NetBearType:{BEARER_WIFI:1,BEARER_ETHERNET:2},
  createNetConnection(){const listeners=new Map(),monitor={listeners,unregistered:0,on:(name,callback)=>listeners.set(name,callback),register:async()=>{},unregister:async()=>{monitor.unregistered++;}};monitors.push(monitor);return monitor;},
  getDefaultNet:async()=>({netId:handle}),getNetCapabilities:async()=>pending?await pending:caps
 }});
 const source=fs.readFileSync('mobile/harmony/entry/src/main/ets/services/NetworkPolicy.ets','utf8').replace(/^import .*;\r?\n/gm,'').replace('export class','class');
 vm.runInContext(stripTypeScriptTypes(source)+';globalThis.NetworkPolicy=NetworkPolicy;',context);
 const network=new context.NetworkPolicy(()=>changed++);await network.start();assert.equal(network.unmetered,true);const wifi=network.epoch;
 caps={networkCap:[1],bearerTypes:[3]};monitors[0].listeners.get('netCapabilitiesChange')();await new Promise(setImmediate);
 assert.equal(network.available,true);assert.equal(network.unmetered,false);assert.notEqual(network.epoch,wifi);
 let resolve;pending=new Promise(done=>resolve=done);monitors[0].listeners.get('netCapabilitiesChange')();await new Promise(setImmediate);
 network.stop();const atStop=changed;resolve({networkCap:[1],bearerTypes:[1]});await new Promise(setImmediate);assert.equal(changed,atStop);assert.equal(network.unmetered,false);
 pending=null;handle=0;await network.start();assert.equal(network.available,false);assert.equal(monitors.length,2);assert.equal(monitors[0].unregistered,1);network.stop();
});
