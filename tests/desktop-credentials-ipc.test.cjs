'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const main=path.resolve(__dirname,'../desktop/main.cjs'),requireMain=require('node:module').createRequire(main);
function boot(){
 const handlers={},calls=[];let instance;
 class Window{constructor(){instance=this;this.webContents={mainFrame:{url:require('node:url').pathToFileURL(path.resolve(__dirname,'../desktop/index.html')).href},setWindowOpenHandler(){},on(){},once(){}};}on(){}loadFile(){}}
 const scope=vm.createContext({__dirname:path.dirname(main),process:{env:{},platform:'darwin'},require(name){
  if(name==='electron')return {app:{requestSingleInstanceLock:()=>true,whenReady:()=>({then(){}}),on(){},getPath:()=>'.tmp'},nativeTheme:{shouldUseDarkColors:false},BrowserWindow:Window,ipcMain:{handle:(name,handler)=>handlers[name]=handler}};
  if(name==='./features/connections/qr.cjs')return {};return requireMain(name);
 }});
 vm.runInContext(fs.readFileSync(main,'utf8'),scope);scope.createWindow();scope.register();
 scope.worker=async(action,payload)=>{calls.push({action,payload});if(action==='snapshot')return {preferences:{connections:[{id:'saved'}]}};if(action==='read-credentials')return {password:'stored'};return {message:'migrated'};};
 return {scope,handlers,calls,event:{sender:instance.webContents,senderFrame:instance.webContents.mainFrame}};
}
test('explicit credential migration traverses the real registered IPC and clears only the old memory value',async()=>{
 const {scope,handlers,calls,event}=boot(),write=handlers['bridge:connection-credentials'],read=handlers['bridge:read-credentials'];
 await write(event,{id:'saved',remember:false,secrets:{password:'memory'}});assert.equal((await read(event,{id:'saved'})).password,'memory');calls.length=0;
 assert.equal((await write(event,{id:'saved',migrate:true})).message,'migrated');
 assert.deepEqual(JSON.parse(JSON.stringify(calls)),[{action:'snapshot'},{action:'connection-credentials',payload:{id:'saved',migrate:true}}]);
 assert.equal((await read(event,{id:'saved'})).password,'stored');
 await assert.rejects(write({...event,senderFrame:{}},{id:'saved',migrate:true}),/不允许的界面来源/);
 await assert.rejects(write(event,{id:'missing',migrate:true}),/先保存/);
 await assert.rejects(write(event,{id:'saved',migrate:true,secrets:{password:'bad'}}),/迁移格式/);
});
test('a late migration response cannot erase a newer in-memory credential',async()=>{
 const {scope,handlers,event}=boot(),original=scope.worker;let finish;
 scope.worker=async(action,payload)=>action==='connection-credentials'&&payload.migrate?new Promise(resolve=>finish=resolve):original(action,payload);
 const pending=handlers['bridge:connection-credentials'](event,{id:'saved',migrate:true});await new Promise(setImmediate);
 await handlers['bridge:connection-credentials'](event,{id:'saved',remember:false,secrets:{password:'new-memory'}});
 finish({message:'migrated'});await pending;
 assert.equal((await handlers['bridge:read-credentials'](event,{id:'saved'})).password,'new-memory');
});
