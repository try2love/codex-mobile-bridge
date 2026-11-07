'use strict';
const test=require('node:test'),assert=require('node:assert/strict');
const {EventEmitter}=require('node:events'),{PassThrough}=require('node:stream');
const {serveWindowsLocal}=require('../desktop/shared/local-service-windows.cjs');
const encode=value=>{const data=Buffer.from(JSON.stringify(value)),head=Buffer.alloc(4);head.writeUInt32BE(data.length);return Buffer.concat([head,data]);};
function fixture(start=true){
 const child=new EventEmitter();Object.assign(child,{stdin:new PassThrough(),stdout:new PassThrough(),stderr:new PassThrough(),exitCode:null,pid:42});
 const messages=[];let buffer=Buffer.alloc(0),args;
 child.stdin.on('data',chunk=>{buffer=Buffer.concat([buffer,chunk]);while(buffer.length>=4&&buffer.length>=buffer.readUInt32BE(0)+4){const size=buffer.readUInt32BE(0);messages.push(JSON.parse(buffer.subarray(4,size+4)));buffer=buffer.subarray(size+4);}});
 child.stdin.on('finish',()=>{child.exitCode=0;child.emit('close',0);});
 return {child,messages,get args(){return args;},launch(executable,argv,options){args={executable,argv,options};setImmediate(()=>start?child.stdout.write(encode({type:'ready'})):child.emit('close',1));return child;}};
}
test('Windows holder keeps private startup arguments and shared handler semantics',async()=>{
 const io=fixture(),calls=[];const stop=await serveWindowsLocal('F:\\private profile','zcode-official',request=>{calls.push(request);return {connected:true};},{executable:'gateway.exe',prefix:['desktop.py']},{launch:io.launch});
 assert.deepEqual(io.args.argv,['desktop.py','local-service-host','--data-dir','F:\\private profile','--name','zcode-official','--parent-pid',String(process.pid)]);assert.equal(io.args.options.windowsHide,true);
 io.child.stdout.write(encode({type:'request',id:'a'.repeat(32),request:{command:'status'}}));await new Promise(setImmediate);
 assert.deepEqual(calls,[{command:'status'}]);assert.deepEqual(io.messages,[{id:'a'.repeat(32),reply:{ok:true,value:{connected:true}}}]);
 stop();assert.equal(io.child.stdin.writableEnded,true);assert.equal(stop.alive(),false);
});
test('Windows holder redacts callback errors and rejects malformed dispatch frames',async()=>{
 const io=fixture();let calls=0;const stop=await serveWindowsLocal('profile','zcode-official',()=>{calls++;throw Error('private-secret');},{executable:'gateway.exe'},{launch:io.launch});
 io.child.stdout.write(encode({type:'request',id:'b'.repeat(32),request:{command:'call'}}));await new Promise(setImmediate);
 assert.equal(io.messages[0].reply.ok,false);assert.equal(JSON.stringify(io.messages).includes('private-secret'),false);
 io.child.stdout.write(encode({type:'request',id:'not-a-request',request:{}}));await new Promise(setImmediate);assert.equal(stop.alive(),false);assert.equal(calls,1);
});
test('a failed Windows holder fails closed without terminating its desktop parent',async()=>{
 const io=fixture(false);await assert.rejects(serveWindowsLocal('profile','zcode-official',()=>{}, {executable:'missing.exe'},{launch:io.launch}),/本机控制接口/);assert.equal(io.child.stdin.writableEnded,true);
});
