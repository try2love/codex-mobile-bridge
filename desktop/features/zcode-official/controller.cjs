'use strict';
const fs=require('node:fs'),path=require('node:path');
const os=require('node:os');
const {randomUUID}=require('node:crypto');
const {pathToFileURL}=require('node:url');
const {ORIGIN,parseLink}=require('./link.cjs');
const {serveLocal}=require('../../shared/local-service.cjs');
const ACTIONS=new Set(['application','projects','target','account','lifecycle','attach','detach','list','create','detail','history','receipt','tasks','stop-task','catalog','send','stop','respond','settings','access','work-mode','goal','compact','fork','edit-message','rename']);
const failure=message=>Object.assign(Error(message),{publicMessage:message});
class OfficialRemote {
  constructor({electron,profile,rendererPath}){
    this.electron=electron;this.profile=profile;this.rendererPath=rendererPath;
    this.authFile=path.join(profile,'zcode-official-auth.enc');this.reason='请登录与 ZCode 原 App 相同的官方账号。';
    this.generation=0;this.runtime=null;this.loginWindow=null;this.auth=null;
  }
  config(){try{return JSON.parse(fs.readFileSync(path.join(this.profile,'desktop-sessions/settings.json'),'utf8'));}catch{return {};}}
  localLink(value){
    const parsed=parseLink(value),config=this.config();
    const home=config.discovered?.zcode?.dataDirectory||config.zcodePrototype?.dataDirectory||path.join(os.homedir(),'.zcode/v2');
    let mid;try{mid=JSON.parse(fs.readFileSync(path.join(home,'telemetry-state.json'),'utf8')).deviceMid;}catch{}
    if(!mid||mid!==parsed.mid)throw failure('此实验只接入本机 ZCode，请使用本机原 App 的官方远控链接。');
    return parsed;
  }
  status(){return {configured:!!this.auth,connected:!!this.runtime,owner:this.runtime?this.owner:null,
    accountName:this.auth?.name||'',provider:this.auth?.provider||'',reason:this.reason,
    link:this.auth?.link||'',selected:this.config().zcodeOfficialRemote===true};}
  secure(){const s=this.electron.safeStorage;
    return s.isEncryptionAvailable()&&(process.platform!=='linux'||s.getSelectedStorageBackend()!=='basic_text');}
  async start(){
    if(process.platform==='win32')return; // This isolated build exposes the tested macOS path only.
    try{if(fs.existsSync(this.authFile)&&this.secure())this.auth=JSON.parse(this.electron.safeStorage.decryptString(fs.readFileSync(this.authFile)));}catch{this.reason='官方登录无法读取，请重新登录。';}
    this.stopServer=await serveLocal(this.profile,'zcode-official',request=>this.handle(request));
  }
  async disconnect(){
    this.generation++;this.runtime?.dispose();this.runtime=null;this.owner=null;this.environment=null;
    this.reason='官方远控已断开，可重新连接。';return this.status();
  }
  async connect(){
    if(this.runtime)return this.owner;
    if(this.connecting)return this.connecting;
    if(!this.auth)throw failure('请先在网关的 ZCode 管理页登录官方远控。');
    this.localLink(this.auth.link);
    const generation=this.generation;
    const task=(async()=>{
      this.reason='正在连接 ZCode 官方远控…';
      const {createRelay}=await import('./runtime.mjs');
      let runtime;
      try{
        runtime=await createRelay({jwt:this.auth.jwt,link:this.auth.link,onState:state=>{
          if(this.runtime!==runtime)return;
          if(state.connected===false){this.reason=state.reason||'官方远控已断开，请重新连接。';this.runtime=null;this.owner=null;this.environment=null;}
        }});
        if(generation!==this.generation){runtime.dispose();throw failure('此次连接已取消。');}
        this.runtime=runtime;this.owner={epoch:randomUUID(),device:parseLink(this.auth.link).sid};
        this.environment={services:runtime.services,contexts:[],state:runtime.state,storage:runtime.storage,
          acquireWorkspace:runtime.acquireWorkspace,clientMode:'web-remote-replayable'};
        this.reason='已连接官方远控';return this.owner;
      }catch(error){runtime?.dispose();this.reason='官方远控连接失败，请检查原 App 的远控开关、账号和网络。';throw failure(this.reason);}
    })();
    this.connecting=task;
    try{return await task;}finally{if(this.connecting===task)this.connecting=null;}
  }
  async handle(message){
    if(message?.command==='status')return this.status();
    if(message?.command==='connect')return this.connect();
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
    const parsed=this.localLink(link);
    if(!this.secure())throw failure('系统安全存储不可用，暂不能保存官方登录。');
    if(this.loginWindow){this.loginWindow.show();this.loginWindow.focus();return this.status();}
    await this.disconnect();
    const {BrowserWindow,session}=this.electron;
    const browserSession=session.fromPartition('zcode-official-login-'+randomUUID());
    browserSession.setPermissionRequestHandler((_w,_p,done)=>done(false));browserSession.setPermissionCheckHandler(()=>false);
    const win=new BrowserWindow({width:1000,height:760,title:'ZCode 官方远控登录',webPreferences:{session:browserSession,nodeIntegration:false,contextIsolation:true,sandbox:true}});
    this.loginWindow=win;
    const allowed=new Set([ORIGIN,'https://chat.z.ai','https://bigmodel.cn','https://open.bigmodel.cn','https://accounts.google.com','https://github.com']);
    const allow=url=>{try{return allowed.has(new URL(url).origin);}catch{return false;}};
    win.webContents.on('will-navigate',(event,url)=>{if(!allow(url))event.preventDefault();});
    win.webContents.on('will-redirect',(event,url)=>{if(!allow(url))event.preventDefault();});
    win.webContents.setWindowOpenHandler(()=>({action:'deny'}));
    let capturing=false;
    const capture=async()=>{
      if(capturing||win.isDestroyed())return;
      let url;try{url=new URL(win.webContents.getURL());}catch{return;}
      if(url.origin!==ORIGIN||url.pathname!=='/remote/v4')return;
      capturing=true;
      try{
        const data=await win.webContents.executeJavaScript(`(()=>{const provider=localStorage.getItem('oauth:active_provider');const jwt=localStorage.getItem('zcodejwttoken');if(!jwt||!['zai','bigmodel'].includes(provider))return null;const user=JSON.parse(localStorage.getItem('oauth:'+provider+':user_info')||'{}');return {jwt,provider,name:user.name||user.displayName||''};})()`);
        if(!data||this.loginWindow!==win||win.isDestroyed())return;
        if(typeof data.jwt!=='string'||data.jwt.length>16384||/\s/.test(data.jwt))throw Error('Invalid token');
        // Close the official web terminal before the gateway claims its own terminal.
        const auth={...data,link:parsed.url};const encrypted=this.electron.safeStorage.encryptString(JSON.stringify(auth));
        fs.writeFileSync(this.authFile+'.tmp',encrypted,{mode:0o600});fs.renameSync(this.authFile+'.tmp',this.authFile);
        this.auth=auth;this.reason='官方登录已保存，点击“连接官方远控”继续。';
        clearInterval(timer);this.loginWindow=null;win.destroy();
      }catch{this.reason='官方登录尚未保存，请检查账号或重新登录。';}
      finally{capturing=false;}
    };
    const timer=setInterval(capture,1200);timer.unref();
    win.on('closed',()=>{clearInterval(timer);if(this.loginWindow===win)this.loginWindow=null;});
    win.webContents.on('did-finish-load',capture);
    await win.loadURL(parsed.url);return this.status();
  }
  async forget(){this.loginWindow?.destroy();this.loginWindow=null;await this.disconnect();this.auth=null;try{fs.unlinkSync(this.authFile);}catch(error){if(error.code!=='ENOENT')throw error;}return this.status();}
  dispose(){this.disconnect();this.loginWindow?.destroy();this.stopServer?.();}
}
module.exports={OfficialRemote};
