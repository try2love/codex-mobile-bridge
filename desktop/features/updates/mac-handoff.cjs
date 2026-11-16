'use strict';
const fs=require('node:fs'),path=require('node:path');
const {activeUpdateHandoff}=require('./updater.cjs');
function launchReceipt(env,pid){
  try{
    const value=JSON.parse(fs.readFileSync(path.join(env.CMB_UPDATE_TRANSACTION,'launch-services.json'),'utf8'));
    return value.pid===pid&&value.launcherPid===Number(env.CMB_UPDATE_LAUNCHER_PID)&&value.token===env.CMB_UPDATE_TOKEN;
  }catch{return false;}
}
function relaunchMacUpdate({app,process:runtime=process}){
  if(runtime.platform!=='darwin'||!app.isPackaged||runtime.env.CMB_UPDATE_LAUNCHER_PID)return;
  const target=path.resolve(runtime.execPath,'../../..');
  const handoff=activeUpdateHandoff(runtime.env,target,app.getVersion(),runtime.pid);
  if(!handoff)return;
  // Before opening a window, a worker or the single-instance lock, replace the
  // old updater's direct child. Keeping its PID preserves old-helper rollback.
  const helper=path.join(runtime.resourcesPath,'gateway/_internal/client-helpers/update-launcher');
  runtime.execve(helper,[helper,target,handoff.transaction],runtime.env);
}
async function restoreMacUpdate({handoff,env=process.env,pid=process.pid,worker,stillManaged}){
  if(!env.CMB_UPDATE_LAUNCHER_PID)return;
  for(let count=0;!launchReceipt(env,pid);count++){
    if(count>=30||!stillManaged())throw Error('更新启动身份尚未确认');
    await new Promise(resolve=>setTimeout(resolve,100));
  }
  const plan=JSON.parse(fs.readFileSync(path.join(handoff.transaction,'plan.json'),'utf8'));
  if(plan.runtime?.running){
    if(!stillManaged())throw Error('更新交接已失效');
    // The new GUI owns the gateway's privacy identity. Older update helpers'
    // subsequent start request is idempotent and attaches to this same gateway.
    await worker('start');
    for(let count=0;;count++){
      if(!stillManaged())throw Error('更新交接已失效');
      if((await worker('snapshot')).runtime?.running)return;
      if(count>=40)throw Error('更新后的网关尚未就绪');
      await new Promise(resolve=>setTimeout(resolve,200));
    }
  }
}
module.exports={relaunchMacUpdate,restoreMacUpdate,launchReceipt};
