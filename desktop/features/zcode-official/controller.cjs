'use strict';
const fs=require('node:fs'),path=require('node:path');
const os=require('node:os');
const {randomUUID}=require('node:crypto');
const {pathToFileURL}=require('node:url');
const {ORIGIN,parseLink}=require('./link.cjs');
const {serveLocal}=require('../../shared/local-service.cjs');
const ACTIONS=new Set(['application','projects','target','account','quota','lifecycle','attach','detach','list','create','detail','history','receipt','tasks','stop-task','catalog','send','stop','respond','settings','access','work-mode','goal','compact','fork','edit-message','rename']);
const failure=message=>Object.assign(Error(message),{publicMessage:message});
class OfficialRemote {
  constructor({electron,profile,rendererPath,localServiceWorker}){
    this.electron=electron;this.profile=profile;this.rendererPath=rendererPath;this.localServiceWorker=localServiceWorker;this.serviceAvailable=false;
    this.authFile=path.join(profile,'zcode-official-auth.enc');this.settingsFile=path.join(profile,'zcode-official.json');
    this.link='';this.reason='请登录与 ZCode 原 App 相同的官方账号。';
    this.generation=0;this.authGeneration=0;this.runtime=null;this.loginWindow=null;this.auth=null;
    this.secureStorageAvailable=null;this.storageCheck=null;this.restorePending=null;this.storageTimeoutMs=12000;this.disposed=false;
  }
  config(){try{return JSON.parse(fs.readFileSync(path.join(this.profile,'desktop-sessions/settings.json'),'utf8'));}catch{return {};}}
  localLink(value){
    const parsed=parseLink(value),config=this.config();
    const home=config.discovered?.zcode?.dataDirectory||config.zcodePrototype?.dataDirectory||path.join(os.homedir(),'.zcode/v2');
    let mid;try{mid=JSON.parse(fs.readFileSync(path.join(home,'telemetry-state.json'),'utf8')).deviceMid;}catch{}
    if(!mid||mid!==parsed.mid)throw failure('此实验只接入本机 ZCode，请使用本机原 App 的官方远控链接。');
    return parsed;
  }
  targetLink(){return this.link||this.auth?.link||'';}
  status(){const config=this.config(),available=this.serviceAvailable&&this.stopServer?.alive?.()!==false;return {configured:!!this.auth&&!!this.targetLink(),connected:available&&!!this.runtime,owner:available&&this.runtime?this.owner:null,available,secureStorageAvailable:this.secureStorageAvailable,
    secureStoragePending:!!this.storageCheck,restoring:!!this.restorePending,loginSaved:fs.existsSync(this.authFile),
    accountName:this.auth?.name||'',provider:this.auth?.provider||'',reason:this.reason,
    link:this.targetLink(),selected:config.zcodeConnectionMode==='official-remote'||!config.zcodeConnectionMode&&config.zcodeOfficialRemote===true};}
  storageBackendAllowed(){return process.platform!=='linux'||['gnome_libsecret','kwallet','kwallet5','kwallet6'].includes(this.electron.safeStorage.getSelectedStorageBackend?.());}
  waitStorage(task){let timer;return Promise.race([task,new Promise((_,reject)=>{timer=setTimeout(()=>reject(failure('系统安全存储尚未就绪，请解锁系统钥匙串后重试。')),this.storageTimeoutMs);})]).finally(()=>clearTimeout(timer));}
  async secure(){
    const storage=this.electron.safeStorage;
    if(!this.storageBackendAllowed()||typeof storage.isAsyncEncryptionAvailable!=='function'||typeof storage.encryptStringAsync!=='function'||typeof storage.decryptStringAsync!=='function'){
      this.secureStorageAvailable=false;return false;
    }
    if(!this.storageCheck){
      const task=Promise.resolve().then(()=>storage.isAsyncEncryptionAvailable()).then(value=>{
        const available=value===true&&this.storageBackendAllowed();
        if(!this.disposed)this.secureStorageAvailable=available;
        return available;
      },()=>{if(!this.disposed)this.secureStorageAvailable=false;return false;});
      this.storageCheck=task;task.finally(()=>{if(this.storageCheck===task)this.storageCheck=null;});
    }
    try{return await this.waitStorage(this.storageCheck);}
    catch(error){this.secureStorageAvailable=false;throw error;}
  }
  encrypted(data){
    if(!Buffer.isBuffer(data)||!data.length||process.platform==='linux'&&data.subarray(0,3).toString()!=='v11'){this.secureStorageAvailable=false;throw failure('系统安全存储不可用，暂不能保存官方登录。');}
    return data;
  }
  fileIdentity(file){try{const stat=fs.statSync(file);return [stat.dev,stat.ino,stat.size,stat.mtimeMs,stat.ctimeMs].join(':');}catch(error){if(error.code==='ENOENT')return null;throw error;}}
  authCurrent(generation,authIdentity,settingsIdentity){try{return !this.disposed&&this.authGeneration===generation&&this.fileIdentity(this.authFile)===authIdentity&&this.fileIdentity(this.settingsFile)===settingsIdentity;}catch{return false;}}
  restore(){
    if(this.disposed)return Promise.resolve(false);
    if(this.restorePending)return this.restorePending;
    try{if(fs.existsSync(this.settingsFile))this.link=this.localLink(JSON.parse(fs.readFileSync(this.settingsFile,'utf8')).link).url;}catch{this.reason='官方远控链接无法读取，请重新保存本机链接。';}
    let authIdentity,settingsIdentity;const generation=this.authGeneration;
    try{authIdentity=this.fileIdentity(this.authFile);settingsIdentity=this.fileIdentity(this.settingsFile);}
    catch{this.reason='官方登录无法读取，请重新登录。';return Promise.resolve(false);}
    if(authIdentity===null)return Promise.resolve(false);
    const task=(async()=>{
      try{
        this.reason='正在恢复已保存的官方登录…';
        const encrypted=this.encrypted(fs.readFileSync(this.authFile));
        if(!await this.secure())throw failure('系统安全存储不可用，暂不能恢复官方登录。');
        if(!this.authCurrent(generation,authIdentity,settingsIdentity))return false;
        const decrypted=await this.waitStorage(this.electron.safeStorage.decryptStringAsync(encrypted));
        if(!this.authCurrent(generation,authIdentity,settingsIdentity))return false;
        const auth=JSON.parse(decrypted.result);
        if(!auth||typeof auth.jwt!=='string'||!auth.jwt||auth.jwt.length>16384||/\s/.test(auth.jwt)||!['zai','bigmodel'].includes(auth.provider))throw Error('Invalid saved login');
        const link=this.localLink(auth.link).url;
        if(!this.link)this.writeLink(link);
        this.auth=auth;this.reason='官方登录已保存，可连接官方远控。';return true;
      }catch(error){if(this.authCurrent(generation,authIdentity,settingsIdentity)){this.auth=null;this.reason=error.publicMessage||'官方登录无法读取，请重新登录。';}return false;}
    })();
    this.restorePending=task;const settled=()=>{if(this.restorePending===task)this.restorePending=null;};task.then(settled,settled);return task;
  }
  save(link){
    const parsed=this.localLink(link);
    if((this.runtime||this.connecting||this.loginWindow)&&parsed.url!==this.targetLink())throw failure('请先断开官方远控并关闭登录窗口，再修改连接配置。');
    if(parsed.url!==this.targetLink())this.authGeneration++;
    this.writeLink(parsed.url);return this.status();
  }
  writeLink(link){
    fs.writeFileSync(this.settingsFile+'.tmp',JSON.stringify({link}),{mode:0o600});
    fs.renameSync(this.settingsFile+'.tmp',this.settingsFile);this.link=link;
  }
  async start(){
    if(this.disposed)return false;
    if(this.serviceAvailable&&this.stopServer?.alive?.()!==false)return true;
    this.stopServer?.();this.serviceAvailable=false;
    try{const stop=await serveLocal(this.profile,'zcode-official',request=>this.handle(request),{worker:this.localServiceWorker});if(this.disposed){stop();return false;}this.stopServer=stop;this.serviceAvailable=true;void this.restore();return true;}
    catch{this.reason='本机控制接口暂不可用，请重试；其他网关功能不受影响。';return false;}
  }
  async disconnect(){
    this.generation++;this.connectAbort?.abort();this.runtime?.dispose();this.runtime=null;this.owner=null;this.environment=null;
    this.reason='官方远控已断开，可重新连接。';return this.status();
  }
  async connect({waitForDevice=false}={}){
    if(this.runtime)return this.owner;
    if(this.connecting)return this.connecting;
    const expectedGeneration=this.generation,expectedAuthGeneration=this.authGeneration;
    if(this.restorePending)await this.restorePending;
    if(expectedGeneration!==this.generation||expectedAuthGeneration!==this.authGeneration)throw failure('此次连接已取消。');
    if(this.disposed)throw failure('官方远控连接服务已关闭。');
    if(this.runtime)return this.owner;
    if(this.connecting)return this.connecting;
    if(!this.auth)throw failure('请先在网关的 ZCode 管理页登录官方远控。');
    const link=this.localLink(this.targetLink()).url;
    const generation=this.generation;
    const connectAbort=this.connectAbort=new AbortController();
    const task=(async()=>{
      this.reason='正在连接 ZCode 官方远控…';
      const {createRelay}=await import('./runtime.mjs');
      let runtime;
      try{
        runtime=await createRelay({jwt:this.auth.jwt,link,waitForDevice,signal:connectAbort.signal,onState:state=>{
          if(this.runtime!==runtime)return;
          if(state.connected===false){this.reason=state.reason||'官方远控已断开，请重新连接。';this.runtime=null;this.owner=null;this.environment=null;}
        }});
        if(generation!==this.generation){runtime.dispose();throw failure('此次连接已取消。');}
        this.runtime=runtime;this.owner={epoch:randomUUID(),device:parseLink(link).sid};
        this.environment={services:runtime.services,contexts:[],state:runtime.state,storage:runtime.storage,
          acquireWorkspace:runtime.acquireWorkspace,clientMode:'web-remote-replayable'};
        this.reason='已连接官方远控';return this.owner;
      }catch(error){runtime?.dispose();if(generation!==this.generation)throw failure('此次连接已取消。');this.reason=({
        'auth-required':'官方登录已失效，请在电脑网关重新登录。',
        'device-offline':'ZCode 官方远控尚未上线，请确认原 App 已开启 Web 远程控制。',
        'device-not-owned':'官方账号或远控链接不匹配，请在电脑网关核对配置。',
        'terminal-replaced':'官方远控被另一终端接入，请确认后重新连接。',
        'connection-timeout':'等待 ZCode 官方远控上线超时，请稍后重新连接。'
      })[error.code]||'官方远控连接失败，请检查原 App 的远控开关、账号和网络。';throw failure(this.reason);}
    })();
    this.connecting=task;
    try{return await task;}finally{if(this.connecting===task){this.connecting=null;this.connectAbort=null;}}
  }
  async handle(message){
    if(message?.command==='status')return this.status();
    if(message?.command==='connect')return this.connect({waitForDevice:message.waitForDevice===true});
    if(message?.command==='disconnect')return this.disconnect();
    if(message?.command!=='call'||!ACTIONS.has(message.request?.action))throw failure('不支持的官方远控操作。');
    const request=message.request;
    if(!this.runtime||JSON.stringify(request.expectedProcess)!==JSON.stringify(this.owner))throw failure('官方远控连接已变化，请重新接入会话。');
    if(!Number.isFinite(request.expiresAt)||request.expiresAt<=Date.now()||request.expiresAt>Date.now()+180000)throw failure('本次请求已过期，请核对原操作结果。');
    const {nativeRequest}=await import(pathToFileURL(this.rendererPath));
    let timer;
    try{return await Promise.race([nativeRequest(request,this.environment),new Promise((_,reject)=>{timer=setTimeout(()=>reject(failure('官方远控回执超时，请核对原操作结果；不会自动重发。')),request.expiresAt-Date.now());})]);}
    finally{clearTimeout(timer);}
  }
  async login(link){
    if(this.disposed)throw failure('官方远控连接服务已关闭。');
    const parsed=this.localLink(link);
    const loginGeneration=this.loginWindow?this.authGeneration:++this.authGeneration;
    if(!await this.secure())throw failure('系统安全存储不可用，暂不能保存官方登录。');
    if(this.disposed||loginGeneration!==this.authGeneration)throw failure('此次登录已取消。');
    if(this.loginWindow){this.loginWindow.show();this.loginWindow.focus();return this.status();}
    if(!this.auth&&fs.existsSync(this.authFile)){
      if(this.restorePending)await this.restorePending;
      if(this.disposed||loginGeneration!==this.authGeneration)throw failure('此次登录已取消。');
      await this.restore();
      if(this.disposed||loginGeneration!==this.authGeneration)throw failure('此次登录已取消。');
      if(this.auth)return this.status();
      if(!await this.secure())throw failure('系统安全存储不可用，暂不能保存官方登录。');
      if(this.disposed||loginGeneration!==this.authGeneration)throw failure('此次登录已取消。');
    }
    await this.disconnect();
    this.save(parsed.url);
    const {BrowserWindow,session}=this.electron;
    const browserSession=session.fromPartition('zcode-official-login-'+randomUUID());
    browserSession.setPermissionRequestHandler((_w,_p,done)=>done(false));browserSession.setPermissionCheckHandler(()=>false);
    const win=new BrowserWindow({width:1000,height:760,title:'ZCode 官方远控登录',webPreferences:{session:browserSession,nodeIntegration:false,contextIsolation:true,sandbox:true}});
    this.loginWindow=win;
    const generation=this.authGeneration,authIdentity=this.fileIdentity(this.authFile),settingsIdentity=this.fileIdentity(this.settingsFile);
    const allowed=new Set([ORIGIN,'https://chat.z.ai','https://bigmodel.cn','https://open.bigmodel.cn','https://accounts.google.com','https://github.com']);
    const allow=url=>{try{return allowed.has(new URL(url).origin);}catch{return false;}};
    win.webContents.on('will-navigate',(event,url)=>{if(!allow(url))event.preventDefault();});
    win.webContents.on('will-redirect',(event,url)=>{if(!allow(url))event.preventDefault();});
    win.webContents.setWindowOpenHandler(()=>({action:'deny'}));
    let capturing=false,encrypting=null;
    const capture=async()=>{
      if(capturing||encrypting||win.isDestroyed())return;
      let url;try{url=new URL(win.webContents.getURL());}catch{return;}
      if(url.origin!==ORIGIN||url.pathname!=='/remote/v4')return;
      capturing=true;
      try{
        const data=await win.webContents.executeJavaScript(`(()=>{const provider=localStorage.getItem('oauth:active_provider');const jwt=localStorage.getItem('zcodejwttoken');if(!jwt||!['zai','bigmodel'].includes(provider))return null;const user=JSON.parse(localStorage.getItem('oauth:'+provider+':user_info')||'{}');return {jwt,provider,name:user.name||user.displayName||''};})()`);
        if(!data||this.loginWindow!==win||win.isDestroyed()||!this.authCurrent(generation,authIdentity,settingsIdentity))return;
        if(typeof data.jwt!=='string'||data.jwt.length>16384||/\s/.test(data.jwt))throw Error('Invalid token');
        // Close the official web terminal before the gateway claims its own terminal.
        const auth={...data,link:parsed.url};
        const task=Promise.resolve().then(()=>this.electron.safeStorage.encryptStringAsync(JSON.stringify(auth)));
        encrypting=task;const settled=()=>{if(encrypting===task)encrypting=null;};task.then(settled,settled);
        const encrypted=this.encrypted(await this.waitStorage(task));
        if(this.loginWindow!==win||win.isDestroyed()||!this.authCurrent(generation,authIdentity,settingsIdentity)||this.targetLink()!==parsed.url)return;
        fs.writeFileSync(this.authFile+'.tmp',encrypted,{mode:0o600});fs.renameSync(this.authFile+'.tmp',this.authFile);
        this.auth=auth;this.reason='官方登录已保存，点击“连接官方远控”继续。';
        clearInterval(timer);this.loginWindow=null;win.destroy();
      }catch(error){if(this.loginWindow===win&&!win.isDestroyed()&&this.authCurrent(generation,authIdentity,settingsIdentity))this.reason=error.publicMessage||'官方登录尚未保存，请检查账号或重新登录。';}
      finally{capturing=false;}
    };
    const timer=setInterval(capture,1200);timer.unref();
    win.on('closed',()=>{clearInterval(timer);if(this.loginWindow===win){this.loginWindow=null;this.authGeneration++;}});
    win.webContents.on('did-finish-load',capture);
    await win.loadURL(parsed.url);return this.status();
  }
  async forget(){this.authGeneration++;this.loginWindow?.destroy();this.loginWindow=null;await this.disconnect();this.auth=null;try{fs.unlinkSync(this.authFile);}catch(error){if(error.code!=='ENOENT')throw error;}return this.status();}
  dispose(){this.disposed=true;this.authGeneration++;this.serviceAvailable=false;this.disconnect();this.loginWindow?.destroy();this.stopServer?.();}
}
module.exports={OfficialRemote};
