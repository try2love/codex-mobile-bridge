'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const main=path.resolve(__dirname,'../desktop/main.cjs'),requireMain=require('node:module').createRequire(main);

test('optional official storage cannot hold the window or core IPC registration',async()=>{
  const calls=[];let ready,rejectStart;
  class OfficialRemote {
    constructor(){this.serviceAvailable=false;}
    start(){calls.push('official');return new Promise((_resolve,reject)=>{rejectStart=reject;});}
  }
  const app={requestSingleInstanceLock:()=>true,whenReady:()=>({then(fn){ready=fn;}}),on(){},getLocale:()=> 'zh-CN',getPath:()=>'/fixture',isPackaged:false};
  const context=vm.createContext({__dirname:path.dirname(main),process:{env:{},platform:'linux'},calls,
    require(name){
      if(name==='electron')return {app};
      if(name==='./features/zcode-official/controller.cjs')return {OfficialRemote};
      if(name==='./features/connections/qr.cjs')return {};
      if(name==='./shared/gateway-language.cjs')return {publishLanguage(){}};
      if(name==='./login-startup.cjs')return {restoreDevelopmentEnvironment(){},createLoginStartup:()=>({hidden:()=>false})};
      if(name==='./shared/controller.cjs')return {createSnapshotWorker:()=>({}),createManagementWorker:()=>({}),workerFor:()=>({})};
      if(name==='node:fs')return {...fs,readFileSync(){throw Error('No fixture preferences');}};
      return requireMain(name);
    }});
  vm.runInContext(fs.readFileSync(main,'utf8'),context);
  vm.runInContext("loadDataDir=()=>'/fixture';setupUpdater=()=>calls.push('updater');register=()=>calls.push('register');createWindow=()=>calls.push('window');",context);
  let timer;
  const outcome=await Promise.race([ready().then(()=> 'ready'),new Promise(resolve=>{timer=setTimeout(()=>resolve('blocked'),100);})]);
  clearTimeout(timer);
  assert.equal(outcome,'ready');assert.deepEqual(calls,['updater','register','window','official']);
  rejectStart(Error('Synthetic optional storage failure'));await new Promise(setImmediate);
  assert.equal(vm.runInContext('officialRemote.serviceAvailable',context),false);
  assert.equal(calls.filter(value=>value==='window').length,1);
});
