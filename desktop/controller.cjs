'use strict';
const {spawn}=require('node:child_process');
const path=require('node:path');
function runWorker({executable,prefix=[],dataDir},action,payload){
  const allowed=new Set(['shared-relay','read-credentials','server-setup','connection-credentials','snapshot','save','start','stop','logs','test-notification','deployment','export-deployment','check-entry','devices','pairing','account', 'accounts','notification-watches','update-prepare']);
  if(!allowed.has(action))return Promise.reject(Error('未知操作'));
  return new Promise((resolve,reject)=>{
    const child=spawn(executable,[...prefix,action,'--data-dir',dataDir],{stdio:['pipe','pipe','pipe'],windowsHide:true});
    let output='',error='';
    const timer=setTimeout(()=>{child.kill();reject(Error('本地操作超时，请检查运行日志'));},action==='server-setup'?300000:action==='connection-credentials'?60000:action==='update-prepare'?180000:['account','accounts'].includes(action)?110000:action==='notification-watches'?45000:25000);
    child.stdout.setEncoding('utf8');child.stdout.on('data',data=>{output+=data;});
    child.stderr.setEncoding('utf8');child.stderr.on('data',data=>{error+=data;});
    child.on('error',err=>{clearTimeout(timer);reject(Error('无法启动网关运行时：'+err.message));});
    child.on('close',()=>{clearTimeout(timer);try{const value=JSON.parse(output.trim());if(!value.ok)throw Object.assign(Error(value.error),{validation:value.validation});resolve(value.result);}catch(e){reject(output.trim()?e:Error('网关运行时未返回结果，请检查日志或重新安装应用'));}});
    child.stdin.on('error',()=>{});child.stdin.end(payload===undefined?'':JSON.stringify(payload));
  });
}
function workerFor({packaged,resources,root,dataDir}){
  return packaged?{executable:path.join(resources,'gateway',process.platform==='win32'?'codex-mobile-gateway.exe':'codex-mobile-gateway'),dataDir}:
    {executable:process.env.CMB_PYTHON||(process.platform==='win32'?'python':'python3'),prefix:['-B',path.join(root,'desktop.py')],dataDir};
}
// One private read-only worker per controller. Writes remain one-shot commands.
function createSnapshotWorker({launch=spawn,timeout=25000}={}){
  let child,key,pending,buffer='';
  function close(error=Error('状态查询已关闭')){
    const old=child;child=null;key=null;buffer='';
    if(pending){clearTimeout(pending.timer);pending.reject(error);pending=null;}
    old?.kill();
  }
  function read(options){
    const next=JSON.stringify(options);
    if(child&&key!==next)close();
    if(pending)return pending.promise;
    if(!child){
      child=launch(options.executable,[...(options.prefix||[]),'snapshot-stream','--data-dir',options.dataDir],{stdio:['pipe','pipe','pipe'],windowsHide:true});
      key=next;const current=child;
      const failed=error=>{if(child===current)close(error);};
      current.on('error',failed);current.on('close',()=>failed(Error('状态查询进程已退出')));
      current.stdin.on('error',failed);current.stderr.on('data',()=>{});
      current.stdout.setEncoding('utf8');
      current.stdout.on('data',data=>{
        if(child!==current)return;
        buffer+=data;let end;
        while((end=buffer.indexOf('\n'))!==-1){
          const line=buffer.slice(0,end);buffer=buffer.slice(end+1);
          if(!pending)continue;
          const request=pending;pending=null;clearTimeout(request.timer);
          try{const value=JSON.parse(line);if(!value.ok)throw Error(value.error);request.resolve(value.result);}
          catch(error){request.reject(error);}
        }
      });
    }
    let resolve,reject;const promise=new Promise((yes,no)=>{resolve=yes;reject=no;});
    pending={promise,resolve,reject,timer:setTimeout(()=>close(Error('状态查询超时')),timeout)};
    child.stdin.write(JSON.stringify({action:'snapshot'})+'\n');
    return promise;
  }
  return {read,close};
}

module.exports={runWorker,workerFor,createSnapshotWorker};
