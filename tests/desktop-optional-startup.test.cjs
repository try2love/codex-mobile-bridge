'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const main=path.resolve(__dirname,'../desktop/main.cjs'),requireMain=require('node:module').createRequire(main);

for(const paired of [false,true])test('optional official service starts after the window only for saved pairings: '+paired,async()=>{
  const calls=[];let ready,rejectStart;
  class OfficialRemote {
    constructor(){this.serviceAvailable=false;}
    start(){calls.push('official');return new Promise((_resolve,reject)=>{rejectStart=reject;});}
  }
  const app={requestSingleInstanceLock:()=>true,whenReady:()=>({then(fn){ready=fn;}}),on(){},getLocale:()=> 'zh-CN',getPath:()=>'/fixture',isPackaged:false};
  const context=vm.createContext({__dirname:path.dirname(main),process:{env:{},platform:'linux'},calls,
    require(name){
      if(name==='electron')return {app};
      if(name==='./features/zcode-official/controller.cjs'){calls.push('load-official');return {OfficialRemote};}
      if(name==='./features/connections/qr.cjs')return {};
      if(name==='./shared/gateway-language.cjs')return {publishLanguage(){}};
      if(name==='./login-startup.cjs')return {restoreDevelopmentEnvironment(){},createLoginStartup:()=>({hidden:()=>false})};
      if(name==='./shared/controller.cjs')return {createSnapshotWorker:()=>({}),createManagementWorker:()=>({}),workerFor:()=>({})};
      if(name==='node:fs')return {...fs,existsSync:file=>paired&&file===path.join('/fixture','zcode-official-auth.enc'),readFileSync(){throw Error('No fixture preferences');}};
      return requireMain(name);
    }});
  vm.runInContext(fs.readFileSync(main,'utf8'),context);
  vm.runInContext("loadDataDir=()=>'/fixture';setupUpdater=()=>calls.push('updater');register=()=>calls.push('register');createWindow=()=>calls.push('window');",context);
  let timer;
  const outcome=await Promise.race([ready().then(()=> 'ready'),new Promise(resolve=>{timer=setTimeout(()=>resolve('blocked'),100);})]);
  clearTimeout(timer);
  assert.equal(outcome,'ready');assert.deepEqual(calls,['updater','register','window',...(paired?['load-official','official']:[])]);
  if(!paired){assert.equal(vm.runInContext('officialRemote',context),undefined);vm.runInContext('officialService().start().catch(()=>{})',context);}
  rejectStart(Error('Synthetic optional storage failure'));await new Promise(setImmediate);
  assert.equal(vm.runInContext('officialRemote.serviceAvailable',context),false);
  assert.equal(calls.filter(value=>value==='window').length,1);
});

test('loadDataDir 保留合法目录名，已存在的损坏指针明确阻断启动',async t=>{
  const scratch=path.resolve(__dirname,'..','.tmp');fs.mkdirSync(scratch,{recursive:true});
  const tmp=fs.mkdtempSync(path.join(scratch,'cmb-loc-'));
  t.after(()=>fs.rmSync(tmp,{recursive:true,force:true}));
  const resources=path.join(tmp,'resources');fs.mkdirSync(resources,{recursive:true});
  function boot({packaged=false,fsOverride=fs}={}){
    const warnings=[],dialogs=[],calls=[];let ready;
    const app={requestSingleInstanceLock:()=>true,whenReady:()=>({then(fn){ready=fn;}}),on(){},quit(){calls.push('quit');},
      getLocale:()=>'zh-CN',getPath:()=>tmp,getVersion:()=>'2.2.1',isPackaged:packaged};
    const context=vm.createContext({__dirname:path.dirname(main),console:{warn:(...a)=>warnings.push(a),error(){}},
      process:{env:{},platform:'linux',pid:process.pid,execPath:path.join(tmp,'codex-mobile-bridge'),resourcesPath:resources},
      require(name){
        if(name==='electron')return {app,dialog:{showErrorBox(title,detail){dialogs.push(detail);}}};
        if(name==='node:fs')return fsOverride;
        if(name==='./features/connections/qr.cjs')return {};
        if(name==='./shared/gateway-language.cjs')return {publishLanguage(){calls.push('language');}};
        if(name==='./login-startup.cjs')return {restoreDevelopmentEnvironment(){},createLoginStartup:()=>({hidden:()=>false})};
        if(name==='./shared/controller.cjs')return {createSnapshotWorker:()=>({close(){}}),createManagementWorker:()=>({close(){}}),workerFor:()=>({})};
        return requireMain(name);
      }});
    vm.runInContext(fs.readFileSync(main,'utf8'),context);
    return {context,warnings,dialogs,calls,ready};
  }

  // 已存在的损坏指针不得选择另一套资料目录
  for(const content of ['{','null','[]','{}',JSON.stringify({dataDir:null}),JSON.stringify({dataDir:123}),JSON.stringify({dataDir:'   '})]){
    fs.writeFileSync(path.join(tmp,'bridge-location.json'),content);
    const {context,warnings,dialogs,calls,ready}=boot({packaged:true});
    assert.throws(()=>context.loadDataDir(),/无法恢复已保存的数据目录/);
    await ready();
    assert.equal(warnings.length,0);
    assert.equal(dialogs.length,1);
    assert.match(dialogs[0],/无法恢复已保存的数据目录/);
    assert.deepEqual(calls,['quit']);
    assert.equal(fs.existsSync(path.join(tmp,'gateway')),false);
  }
  // 随包 bridge-data-dir.txt 为空白时同样回退默认目录
  fs.rmSync(path.join(tmp,'bridge-location.json'));
  fs.writeFileSync(path.join(resources,'bridge-data-dir.txt'),'   \n');
  assert.equal(boot({packaged:true}).context.loadDataDir(),path.join(tmp,'gateway'));
  // 合法路径中的尾空格属于实际目录名，绑定哨兵保持在同一目录。
  const selected=path.join(tmp,'custom ');fs.mkdirSync(selected);
  fs.writeFileSync(path.join(selected,'zcode-official-auth.enc'),'fixture-binding');
  fs.writeFileSync(path.join(tmp,'bridge-location.json'),JSON.stringify({dataDir:selected}));
  const restored=boot({packaged:true}).context.loadDataDir();
  assert.equal(restored,selected);
  assert.equal(fs.readFileSync(path.join(restored,'zcode-official-auth.enc'),'utf8'),'fixture-binding');
  const unreadable=boot({packaged:true,fsOverride:{...fs,readFileSync(){throw Object.assign(Error('denied'),{code:'EACCES'});}}});
  assert.throws(()=>unreadable.context.loadDataDir(),/无法恢复已保存的数据目录.*denied/);
});

test('saveDataDir 原子替换配置并在重命名失败时保留原配置且清理临时文件',t=>{
  const scratch=path.resolve(__dirname,'..','.tmp');fs.mkdirSync(scratch,{recursive:true});
  const tmp=fs.mkdtempSync(path.join(scratch,'cmb-save-'));
  t.after(()=>fs.rmSync(tmp,{recursive:true,force:true}));
  function boot(fsOverride){
    const app={requestSingleInstanceLock:()=>true,whenReady:()=>({then(){}}),on(){},getLocale:()=>'zh-CN',getPath:()=>tmp,isPackaged:true};
    const context=vm.createContext({__dirname:path.dirname(main),process:{env:{},platform:'linux',pid:4242,resourcesPath:tmp},
      require(name){
        if(name==='electron')return {app};
        if(name==='node:fs')return fsOverride||fs;
        if(name==='./features/connections/qr.cjs')return {};
        if(name==='./shared/gateway-language.cjs')return {publishLanguage(){}};
        if(name==='./login-startup.cjs')return {restoreDevelopmentEnvironment(){},createLoginStartup:()=>({hidden:()=>false})};
        if(name==='./shared/controller.cjs')return {createSnapshotWorker:()=>({close(){}}),createManagementWorker:()=>({close(){}}),workerFor:()=>({})};
        return requireMain(name);
      }});
    vm.runInContext(fs.readFileSync(main,'utf8'),context);
    return context;
  }

  const target=path.join(tmp,'bridge-location.json');
  boot().saveDataDir('/data/first');
  assert.deepEqual(JSON.parse(fs.readFileSync(target,'utf8')),{dataDir:'/data/first'});
  assert.deepEqual(fs.readdirSync(tmp),['bridge-location.json']);

  const original=fs.readFileSync(target);
  const broken=boot({...fs,renameSync(){throw Error('Simulated EACCES on rename');}});
  assert.throws(()=>broken.saveDataDir('/data/second'),/Simulated EACCES/);
  assert.deepEqual(fs.readFileSync(target),original);
  assert.deepEqual(fs.readdirSync(tmp),['bridge-location.json']);
  const writes=[];
  const missing=boot({...fs,writeFileSync(file,...args){
    writes.push(file);assert.notEqual(file,target);
    fs.writeFileSync(file,...args);fs.unlinkSync(file);
  }});
  assert.throws(()=>missing.saveDataDir('/data/third'),{code:'ENOENT'});
  assert.equal(writes.length,1);
  assert.deepEqual(fs.readFileSync(target),original);
  assert.deepEqual(fs.readdirSync(tmp),['bridge-location.json']);
});

test('whenReady 启动异常会被内部捕获，弹窗提示并清理退出，且不会因 activate/second-instance 打开半残窗口',async()=>{
  let ready;const events={},dialogs=[],errors=[];let quits=0,workerCloses=0;
  // 故意只提供 then 而不提供 catch，验证不会依赖 Promise.prototype.catch 链式调用
  const app={requestSingleInstanceLock:()=>true,whenReady:()=>({then(fn){ready=fn;}}),on(name,fn){events[name]=fn;},
    getLocale:()=>'zh-CN',getPath:()=>'/fixture',isPackaged:false,quit(){quits++;}};
  const context=vm.createContext({__dirname:path.dirname(main),console:{warn(){},error:(...a)=>errors.push(a)},
    process:{env:{},platform:'linux',pid:1},calls:[],
    require(name){
      if(name==='electron')return {app,dialog:{showErrorBox(title,detail){dialogs.push({title,detail});}}};
      if(name==='./features/connections/qr.cjs')return {};
      if(name==='./shared/gateway-language.cjs')return {publishLanguage(){}};
      if(name==='./login-startup.cjs')return {restoreDevelopmentEnvironment(){},createLoginStartup:()=>({hidden:()=>false})};
      if(name==='./shared/controller.cjs')return {createSnapshotWorker:()=>({close(){workerCloses++;}}),createManagementWorker:()=>({close(){workerCloses++;}}),workerFor:()=>({})};
      return requireMain(name);
    }});
  vm.runInContext(fs.readFileSync(main,'utf8'),context);
  // 就绪前触发 activate/second-instance 不应打开窗口
  vm.runInContext('showWindow=()=>{calls.push("win");};',context);
  events.activate();events['second-instance']({},[]);
  assert.equal(context.calls.length,0);

  vm.runInContext("loadDataDir=()=>{throw Error('磁盘不可写');};",context);
  await ready();
  assert.equal(quits,1);
  assert.equal(workerCloses,2);
  assert.equal(errors.length,1);
  assert.deepEqual(dialogs,[{title:'网关操作未完成',detail:'磁盘不可写'}]);
  // 启动失败后再次触发 activate/second-instance 仍不得打开窗口
  events.activate();events['second-instance']({},[]);
  assert.equal(context.calls.length,0);
});
