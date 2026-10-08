'use strict';
const test=require('node:test'),assert=require('node:assert/strict');
const fs=require('node:fs/promises'),path=require('node:path'),{spawn}=require('node:child_process');
const root=path.resolve(__dirname,'..'),python=process.platform==='win32'?'python':'python3';
const delay=ms=>new Promise(resolve=>setTimeout(resolve,ms));

async function readEventually(file){
  const end=Date.now()+20000;
  while(Date.now()<end){try{return JSON.parse(await fs.readFile(file,'utf8'));}catch(error){if(error.code!=='ENOENT'&&!(error instanceof SyntaxError))throw error;}await delay(50);}
  throw Error('Timed out reading '+file);
}
async function fixture(t){
  await fs.mkdir(path.join(root,'.tmp'),{recursive:true});
  const work=await fs.mkdtemp(path.join(root,'.tmp/update-handoff-中文 '));
  const target=path.join(work,'Codex Mobile Bridge'),transaction=path.join(work,'.cmb-update-test'),data=path.join(work,'data');
  await Promise.all([target,transaction,data].map(p=>fs.mkdir(p)));
  await fs.writeFile(path.join(target,'version'),'old');
  const helper=path.join(transaction,'helper.py'),plan=path.join(transaction,'plan.json');
  await fs.writeFile(helper,`import json, os, sys, time
from pathlib import Path
plan = json.loads(Path(sys.argv[1]).read_text(encoding='utf-8'))
transaction = Path(sys.argv[1]).parent
(transaction / 'ready.json').write_text(json.dumps({'token': plan['token'], 'cwd': os.getcwd(), 'pid': os.getpid()}), encoding='utf-8')
end = time.monotonic() + 20
while not (transaction / 'go').exists():
    if time.monotonic() >= end: raise TimeoutError('parent did not exit')
    time.sleep(.05)
try:
    Path(plan['target']).rename(transaction / 'previous')
    result = {'state': 'updated'}
except OSError as error:
    result = {'state': 'failed', 'winerror': getattr(error, 'winerror', None), 'message': str(error)}
(transaction / 'result.json').write_text(json.dumps(result), encoding='utf-8')
`);
  t.after(async()=>{
    try{const ready=JSON.parse(await fs.readFile(path.join(transaction,'ready.json'),'utf8'));try{process.kill(ready.pid);}catch(error){if(error.code!=='ESRCH')throw error;}}catch(error){if(error.code!=='ENOENT')throw error;}
    await fs.rm(work,{recursive:true,force:true,maxRetries:20,retryDelay:50});
  });
  return {work,target,transaction,data,helper,plan};
}

test('Windows reproduces WinError 32 when the updater inherits the install directory', {skip:process.platform!=='win32'},async t=>{
  const f=await fixture(t);
  await fs.writeFile(f.plan,JSON.stringify({target:f.target,token:'legacy'}));
  const child=spawn(python,['-B',f.helper,f.plan],{cwd:f.target,stdio:'ignore'});
  const exit=new Promise((resolve,reject)=>{child.once('error',reject);child.once('exit',resolve);});
  await readEventually(path.join(f.transaction,'ready.json'));
  await fs.writeFile(path.join(f.transaction,'go'),'');
  const result=await readEventually(path.join(f.transaction,'result.json'));await exit;
  assert.equal(result.state,'failed');assert.equal(result.winerror,32,JSON.stringify(result));
  assert.equal(await fs.readFile(path.join(f.target,'version'),'utf8'),'old');
});

test('real desktop install handoff releases the install directory after the controller exits',async t=>{
  const f=await fixture(t),main=path.join(root,'desktop/main.cjs');
  // Run the actual main-process handler in a separate controller process with
  // an install-directory cwd, as with a Windows shortcut. Only Electron and
  // preparation are faked; helper creation, inheritance and rename are real.
  const caller=path.join(f.work,'caller.cjs');
  await fs.writeFile(caller,`
const fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const {spawn}=require('node:child_process'),requireMain=require('node:module').createRequire(${JSON.stringify(main)});
const f=${JSON.stringify(f)};
const app={isPackaged:true,requestSingleInstanceLock:()=>true,whenReady:()=>({then(){}}),on(){},getPath:()=>f.data,
  quit:()=>{throw Error('update handoff must not rely on a cancellable graceful quit')},
  exit:code=>process.exit(code)};
const context=vm.createContext({__dirname:path.dirname(${JSON.stringify(main)}),setTimeout,
  process:{env:{},platform:'win32',arch:process.arch,pid:process.pid,execPath:path.join(f.target,'Codex Mobile Bridge.exe')},
  require(name){
    if(name==='electron')return {app};
    if(name==='./qr.cjs')return {};
    if(name==='./controller.cjs')return {createSnapshotWorker:()=>({close(){}}),createManagementWorker:()=>({close(){}}),workerFor:()=>({}),runWorker:async(_,action,payload)=>{
      if(action!=='update-prepare')throw Error(action);
      fs.writeFileSync(f.plan,JSON.stringify(payload));return {helper:f.helper,plan:f.plan};
    }};
    if(name==='node:child_process')return {spawn:(_,args,options)=>spawn(${JSON.stringify(python)},['-B',f.helper,f.plan],{...options,env:process.env})};
    return requireMain(name);
  }});
vm.runInContext(fs.readFileSync(${JSON.stringify(main)},'utf8'),context);
vm.runInContext('dataDir='+JSON.stringify(f.data),context);
context.installUpdate({archive:'verified.zip',asset:{sha256:'verified'},version:'1.0.1'}).catch(error=>{console.error(error);process.exit(1);});
`);
  const child=spawn(process.execPath,[caller],{cwd:f.target,stdio:['ignore','ignore','pipe']});
  let errors='';child.stderr.on('data',chunk=>errors+=chunk);
  t.after(()=>{if(child.exitCode===null)child.kill();});
  const code=await new Promise((resolve,reject)=>{child.once('error',reject);child.once('exit',resolve);});
  assert.equal(code,0,errors);
  const ready=await readEventually(path.join(f.transaction,'ready.json'));
  await fs.writeFile(path.join(f.transaction,'go'),'');
  const result=await readEventually(path.join(f.transaction,'result.json'));
  assert.equal(result.state,'updated',JSON.stringify(result));
  assert.equal(ready.cwd,f.work,'Updater must use the stable install parent, not the installation or transaction directory');
  assert.equal(await fs.readFile(path.join(f.transaction,'previous/version'),'utf8'),'old');
});
