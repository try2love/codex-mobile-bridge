'use strict';
const path=require('node:path');
const fs=require('node:fs');
const developmentConfig=root=>path.join(root,'.local','desktop-login-startup.json');
const argument='--login-startup';
function restoreDevelopmentEnvironment(app,argv=process.argv,env=process.env,platform=process.platform,root=path.resolve(__dirname,'..')){
  if(platform!=='win32'||app.isPackaged||!argv.includes(argument))return;
  if(fs.existsSync(developmentConfig(root))){
    const saved=JSON.parse(fs.readFileSync(developmentConfig(root),'utf8'));
    for(const key of ['CMB_DATA_DIR','CMB_PYTHON'])if(typeof saved[key]==='string'&&saved[key])env[key]=saved[key];
  }
  env.PYTHONUTF8='1';
}
function createLoginStartup(app,{platform=process.platform,executable=process.execPath,root=path.resolve(__dirname,'..'),env=process.env}={}){
  const supported=platform==='win32';
  // Electron development launches need the project entry and the same gateway environment.
  const args=app.isPackaged?[argument]:[root,argument];
  const options={path:executable,args,name:app.isPackaged?'Codex Mobile Bridge':'Codex Mobile Bridge Development'};
  function status(){
    if(!supported)return {supported:false,enabled:false};
    let settings=app.getLoginItemSettings(options);
    // Electron 44's Windows lookup misses quoted executable paths containing spaces.
    if(executable.includes(' ')&&!settings.launchItems?.some(item=>item.name===options.name)){
      settings=app.getLoginItemSettings({...options,path:'"'+executable+'"'});
    }
    // openAtLogin queries Electron's default entry name, even when set uses a custom name.
    const enabled=settings.launchItems?.some(item=>item.name===options.name&&item.scope==='user'&&item.enabled);
    return {supported:true,enabled:!!enabled};
  }
  function validate(enabled){
    if(typeof enabled!=='boolean')throw Error('登录自启开关格式错误');
    if(!supported)throw Error('请在 Windows 上设置登录自启。');
  }
  function set(enabled){
    validate(enabled);
    if(enabled&&!app.isPackaged){
      // Keep Python and data paths out of Windows Run's 260-character command limit.
      const saved={};for(const key of ['CMB_DATA_DIR','CMB_PYTHON'])if(env[key])saved[key]=env[key];
      fs.mkdirSync(path.dirname(developmentConfig(root)),{recursive:true});
      fs.writeFileSync(developmentConfig(root),JSON.stringify(saved),{mode:0o600});
    }
    app.setLoginItemSettings({...options,openAtLogin:enabled});
    if(status().enabled!==enabled)throw Error('Windows 登录自启设置保存失败，请重试。');
  }
  return {status,validate,set,hidden:argv=>supported&&argv.includes(argument)};
}
module.exports={createLoginStartup,restoreDevelopmentEnvironment};
