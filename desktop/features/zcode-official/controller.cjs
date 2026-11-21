'use strict';
const fs=require('node:fs'),path=require('node:path');
const os=require('node:os');
const {randomUUID}=require('node:crypto');
const {PrivateCredentials}=require('../../shared/private-credentials.cjs');
const {pathToFileURL}=require('node:url');
const {ORIGIN,parseLink}=require('./link.cjs');
const {serveLocal}=require('../../shared/local-service.cjs');
const ACTIONS=new Set(['application','projects','target','account','quota','lifecycle','attach','detach','list','create','detail','history','receipt','tasks','stop-task','catalog','send','stop','respond','settings','access','work-mode','goal','compact','fork','edit-message','rename']);
const failure=message=>Object.assign(Error(message),{publicMessage:message});
const LOGIN_PROVIDERS=['zai','zai_cn','bigmodel'];
function authorizationRequest(value){
  try{
    const url=new URL(value),zai=['https://chat.z.ai','https://zchat.z.ai'].includes(url.origin)&&url.pathname==='/api/oauth/authorize';
    const bigmodel=['https://bigmodel.cn','https://open.bigmodel.cn'].includes(url.origin)&&url.pathname==='/login';
    const redirectKey=zai?'redirect_uri':'redirect',state=url.searchParams.get('state');
    if((!zai&&!bigmodel)||url.protocol!=='https:'||url.username||url.password||url.hash||
      url.searchParams.getAll('state').length!==1||!state||state.length>8192||url.searchParams.getAll(redirectKey).length!==1)return null;
    const redirect=new URL(url.searchParams.get(redirectKey));
    if(redirect.origin!==ORIGIN||redirect.protocol!=='https:'||redirect.username||redirect.password||redirect.hash||
      !['/remote/v4','/web-remote/callback','/api/v1/oauth/cli/callback/zai','/api/v1/oauth/cli/callback/zai_cn'].includes(redirect.pathname))return null;
    return {url:url.href,state,redirect:redirect.href};
  }catch{return null;}
}
function readBrowserLogin(providers){
  // Runs inside the official page. A primary record supersedes legacy keys;
  // an incomplete new session must never resurrect an earlier account.
  const primary=localStorage.getItem('oauth:browser_session');
  try{
    if(primary!==null){
      const session=JSON.parse(primary),user=session?.userInfo;
      if(!providers.includes(session?.provider)||typeof session.revision!=='string'||!session.revision.trim()||
        typeof user?.id!=='string'||!user.id.trim()||user.id==='unknown')return null;
      return {jwt:session.zcodeJwtToken,provider:session.provider,name:user.displayName||user.username||user.name||''};
    }
    const provider=localStorage.getItem('oauth:active_provider'),jwt=localStorage.getItem('zcodejwttoken');
    if(!jwt||!providers.includes(provider))return null;
    const user=JSON.parse(localStorage.getItem('oauth:'+provider+':user_info')||'{}');
    return {jwt,provider,name:user.name||user.displayName||''};
  }catch{return null;}
}
class OfficialRemote {
  constructor({electron,profile,rendererPath,localServiceWorker}){
    this.electron=electron;this.profile=profile;this.rendererPath=rendererPath;this.localServiceWorker=localServiceWorker;this.serviceAvailable=false;
    this.localStorage=process.platform==='darwin'?new PrivateCredentials(profile,'zcode-official'):null;
    this.legacyLoginSaved=false;this.legacyAuthFile=path.join(profile,'zcode-official-auth.legacy.enc');
    this.authFile=path.join(profile,'zcode-official-auth.enc');this.settingsFile=path.join(profile,'zcode-official.json');
    this.link='';this.reason='请登录与 ZCode 原 App 相同的官方账号。';
    this.generation=0;this.authGeneration=0;this.runtime=null;this.loginWindow=null;this.auth=null;this.loginExternal=false;this.pendingAuthorization=null;
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
    secureStoragePending:!!this.storageCheck,restoring:!!this.restorePending,loginDeferred:this.loginDeferred===true,loginSaved:fs.existsSync(this.authFile),legacyLoginSaved:this.legacyLoginSaved,
    browserLoginPending:!!this.loginWindow&&this.loginExternal,canSubmitLoginReceipt:!!this.loginWindow&&!!this.pendingAuthorization&&!this.pendingAuthorization.window.isDestroyed()&&!this.pendingAuthorization.used&&Date.now()<this.pendingAuthorization.expiresAt,
    accountName:this.auth?.name||'',provider:this.auth?.provider||'',reason:this.reason,
    link:this.targetLink(),selected:config.zcodeConnectionMode==='official-remote'||!config.zcodeConnectionMode&&config.zcodeOfficialRemote===true};}
  credentialStorage(){return this.localStorage||this.electron.safeStorage;}
  writeAuth(data,previous,authIdentity,settingsIdentity){
    if(this.localStorage&&previous&&!PrivateCredentials.encrypted(previous)){
      if(fs.existsSync(this.legacyAuthFile)){if(!fs.readFileSync(this.legacyAuthFile).equals(previous))throw failure('旧登录备份已存在，请先检查资料目录。');}
      else this.writePrivate(this.legacyAuthFile,previous,()=>{if(fs.existsSync(this.legacyAuthFile))throw failure('旧登录备份已变化，已停止覆盖。');});
    }
    this.writePrivate(this.authFile,data,()=>{
      if(this.fileIdentity(this.authFile)!==authIdentity||this.fileIdentity(this.settingsFile)!==settingsIdentity)throw failure('官方登录或链接已被其他进程修改，已停止覆盖。');
      this.localStorage?.assertCurrent(data);
    });this.legacyLoginSaved=false;
  }
  writePrivate(file,data,verify){
    const temporary=file+'.'+randomUUID()+'.tmp';let fd;
    try{fd=fs.openSync(temporary,'wx',0o600);fs.writeFileSync(fd,data);fs.fsyncSync(fd);fs.closeSync(fd);fd=undefined;verify?.();fs.renameSync(temporary,file);}
    finally{if(fd!==undefined)fs.closeSync(fd);fs.rmSync(temporary,{force:true});}
  }
  storageBackendAllowed(){return process.platform!=='linux'||['gnome_libsecret','kwallet','kwallet5','kwallet6'].includes(this.electron.safeStorage.getSelectedStorageBackend?.());}
  waitStorage(task){let timer;return Promise.race([task,new Promise((_,reject)=>{timer=setTimeout(()=>reject(failure(this.localStorage?'网关凭据存储尚未就绪，请重试。':'系统安全存储尚未就绪，请解锁系统钥匙串后重试。')),this.storageTimeoutMs);})]).finally(()=>clearTimeout(timer));}
  async secure(){
    const storage=this.credentialStorage();
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
    if(!Buffer.isBuffer(data)||!data.length||this.localStorage&&!PrivateCredentials.encrypted(data)||process.platform==='linux'&&data.subarray(0,3).toString()!=='v11'){this.secureStorageAvailable=false;throw failure('系统安全存储不可用，暂不能保存官方登录。');}
    return data;
  }
  fileIdentity(file){try{const stat=fs.statSync(file);return [stat.dev,stat.ino,stat.size,stat.mtimeMs,stat.ctimeMs].join(':');}catch(error){if(error.code==='ENOENT')return null;throw error;}}
  authCurrent(generation,authIdentity,settingsIdentity){try{return !this.disposed&&this.authGeneration===generation&&this.fileIdentity(this.authFile)===authIdentity&&this.fileIdentity(this.settingsFile)===settingsIdentity;}catch{return false;}}
  restore({migrateLegacy=false}={}){
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
        const original=fs.readFileSync(this.authFile),legacy=this.localStorage&&!PrivateCredentials.encrypted(original);
        if(legacy&&!['v10','v11'].includes(original.subarray(0,3).toString()))throw failure('官方登录文件格式无法确认，已保留原文件。');
        this.legacyLoginSaved=!!legacy;
        if(legacy&&!migrateLegacy){this.auth=null;this.legacyLoginSaved=true;this.reason='旧官方登录已保留，请点击“迁移旧登录”，或重新登录后保存到网关。';return false;}
        const storage=legacy?this.electron.safeStorage:this.credentialStorage();
        if(legacy){if(!await this.waitStorage(storage.isAsyncEncryptionAvailable()))throw failure('旧登录迁移需要原系统凭据授权。');}
        else if(!await this.secure())throw failure('凭据存储不可用，暂不能恢复官方登录。');
        if(!this.authCurrent(generation,authIdentity,settingsIdentity))return false;
        const decrypted=await this.waitStorage(storage.decryptStringAsync(legacy?original:this.encrypted(original)));
        if(!this.authCurrent(generation,authIdentity,settingsIdentity))return false;
        if(!legacy)this.localStorage?.assertCurrent(decrypted);
        const auth=JSON.parse(decrypted.result);
        if(!auth||typeof auth.jwt!=='string'||!auth.jwt||auth.jwt.length>16384||/\s/.test(auth.jwt)||!LOGIN_PROVIDERS.includes(auth.provider))throw Error('Invalid saved login');
        const link=this.localLink(auth.link).url;
        if(legacy){
          const encrypted=await this.credentialStorage().encryptStringAsync(JSON.stringify(auth));
          if(!this.authCurrent(generation,authIdentity,settingsIdentity))return false;
          this.writeAuth(encrypted,original,authIdentity,settingsIdentity);
        }
        if(!this.link)this.writeLink(link);
        this.auth=auth;this.reason='官方登录已保存，可连接官方远控。';return true;
      }catch(error){if(this.authCurrent(generation,authIdentity,settingsIdentity)){this.auth=null;this.reason=error.publicMessage||'官方登录无法读取，请重新登录。';}return false;}
    })();
    this.restorePending=task;const settled=()=>{if(this.restorePending===task)this.restorePending=null;};task.then(settled,settled);return task;
  }
  async migrateLogin(){
    if(!this.localStorage)throw failure('当前系统无需迁移官方登录。');
    if(this.restorePending)await this.restorePending;
    this.authGeneration++;
    if(!await this.restore({migrateLegacy:true}))throw failure(this.reason);
    return this.status();
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
  async start({standby=false}={}){
    if(this.disposed)return false;
    if(!this.serviceAvailable||this.stopServer?.alive?.()===false){
      if(!this.startPending)this.startPending=this.startServer().finally(()=>{this.startPending=null;});
      if(!await this.startPending)return false;
    }
    const config=this.config();
    if(!standby||config.enabled?.zcode&&(config.zcodeConnectionMode==='official-remote'||!config.zcodeConnectionMode&&config.zcodeOfficialRemote)){
      this.restoreOnDemand();
    }
    return true;
  }
  restoreOnDemand(){
    if(this.loginDeferred){this.loginDeferred=false;void this.restore();}
  }
  async startServer(){
    this.stopServer?.();this.serviceAvailable=false;
    try{const stop=await serveLocal(this.profile,'zcode-official',request=>this.handle(request),{worker:this.localServiceWorker});if(this.disposed){stop();return false;}this.stopServer=stop;this.serviceAvailable=true;this.loginDeferred=true;return true;}
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
    this.restoreOnDemand();
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
  async completeLoginReceipt(value){
    const pending=this.pendingAuthorization,win=this.loginWindow;
    if(!pending||!win||win.isDestroyed()||pending.window.isDestroyed()||pending.generation!==this.authGeneration||
      !this.authCurrent(pending.generation,pending.authIdentity,pending.settingsIdentity)||this.targetLink()!==pending.link||
      pending.used||Date.now()>=pending.expiresAt)throw failure('授权已结束或过期，请重新开始登录。');
    let url;try{url=new URL(typeof value==='string'&&value.length<=16384?value.trim():'');}catch{throw failure('请粘贴完整的官方授权回调地址。');}
    const expected=new URL(pending.redirect),query=url.searchParams;
    const extras=new Set([...expected.searchParams.keys(),'state','code','authCode']);
    const codes=[...query.getAll('code'),...query.getAll('authCode')];
    if(url.protocol!=='https:'||url.origin!==expected.origin||url.pathname!==expected.pathname||url.username||url.password||url.hash||
      query.getAll('state').length!==1||query.get('state')!==pending.state||codes.length!==1||!codes[0].trim()||
      [...query.keys()].some(key=>!extras.has(key))||[...expected.searchParams.keys()].some(key=>
        JSON.stringify(query.getAll(key))!==JSON.stringify(expected.searchParams.getAll(key))))throw failure('授权回执与本次登录不匹配，请检查浏览器地址。');
    pending.used=true;
    try{await pending.window.loadURL(url.href);}
    catch{if(this.auth===pending.previousAuth)throw failure('回执已提交，结果尚未确认；请等待官方登录完成或重新开始登录。');}
    return this.status();
  }
  async login(link,{external=false}={}){
    if(this.disposed)throw failure('官方远控连接服务已关闭。');
    const parsed=this.localLink(link);
    const loginGeneration=this.loginWindow?this.authGeneration:++this.authGeneration;
    if(!await this.secure())throw failure('系统安全存储不可用，暂不能保存官方登录。');
    if(this.disposed||loginGeneration!==this.authGeneration)throw failure('此次登录已取消。');
    if(this.loginWindow){if(this.loginExternal!==external)throw failure('请先关闭当前登录窗口，再切换登录方式。');this.loginWindow.show();this.loginWindow.focus();return this.status();}
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
    this.loginWindow=win;this.loginExternal=external;this.pendingAuthorization=null;
    const generation=this.authGeneration,authIdentity=this.fileIdentity(this.authFile),settingsIdentity=this.fileIdentity(this.settingsFile);
    const allowed=new Set([ORIGIN,'https://chat.z.ai','https://zchat.z.ai','https://bigmodel.cn','https://open.bigmodel.cn','https://accounts.google.com','https://github.com']);
    const allow=url=>{try{const value=new URL(url);return value.protocol==='https:'&&!value.username&&!value.password&&allowed.has(value.origin);}catch{return false;}};
    const popups=new Set();
    const externalAuthorization=(target,url)=>{
      const request=external&&authorizationRequest(url);
      if(!request||this.loginWindow!==win||!this.authCurrent(generation,authIdentity,settingsIdentity))return false;
      if(this.pendingAuthorization?.url===request.url&&this.pendingAuthorization.window===target)return true;
      const pending=this.pendingAuthorization={...request,window:target,generation,authIdentity,settingsIdentity,link:parsed.url,
        expiresAt:Date.now()+10*60*1000,used:false,previousAuth:this.auth};
      this.reason='请在系统浏览器完成授权；通常会自动返回，也可粘贴完整回调地址。';
      Promise.resolve().then(()=>{
        if(this.pendingAuthorization!==pending||this.loginWindow!==win||win.isDestroyed()||target.isDestroyed()||
          !this.authCurrent(generation,authIdentity,settingsIdentity))return;
        return this.electron.shell.openExternal(request.url);
      }).catch(()=>{
        if(this.pendingAuthorization===pending)this.reason='无法打开系统浏览器，请关闭登录窗口后重试内嵌登录。';
      });
      return true;
    };
    const guard=target=>{
      const navigate=(event,url)=>{
        if(!allow(url)||externalAuthorization(target,url))event.preventDefault();
        else if(external&&target!==win)target.show();
      };
      target.webContents.on('will-navigate',navigate);
      target.webContents.on('will-redirect',navigate);
      target.webContents.setWindowOpenHandler(({url})=>{
        // Official login opens a blank placeholder before its asynchronous
        // authorization request; subsequent navigation still uses the guard.
        if(this.loginWindow!==win||win.isDestroyed()||url!=='about:blank'&&!allow(url))return {action:'deny'};
        return {action:'allow',outlivesOpener:false,
          overrideBrowserWindowOptions:{show:!(external&&(url==='about:blank'||authorizationRequest(url))),webPreferences:{session:browserSession,nodeIntegration:false,contextIsolation:true,sandbox:true}},
          createWindow:options=>{
            const popup=new BrowserWindow(options);popups.add(popup);guard(popup);
            externalAuthorization(popup,url);
            popup.on('closed',()=>popups.delete(popup));return popup.webContents;
          }};
      });
    };
    guard(win);
    let capturing=false,encrypting=null;
    const capture=async()=>{
      if(capturing||encrypting||win.isDestroyed())return;
      let url;try{url=new URL(win.webContents.getURL());}catch{return;}
      if(url.origin!==ORIGIN||url.pathname!=='/remote/v4')return;
      capturing=true;
      try{
        const data=await win.webContents.executeJavaScript(`(${readBrowserLogin.toString()})(${JSON.stringify(LOGIN_PROVIDERS)})`);
        if(!data||this.loginWindow!==win||win.isDestroyed()||!this.authCurrent(generation,authIdentity,settingsIdentity))return;
        if(typeof data.jwt!=='string'||!data.jwt||data.jwt.length>16384||/\s/.test(data.jwt)||!LOGIN_PROVIDERS.includes(data.provider))throw Error('Invalid token');
        // Close the official web terminal before the gateway claims its own terminal.
        const auth={...data,link:parsed.url};
        const task=Promise.resolve().then(()=>this.credentialStorage().encryptStringAsync(JSON.stringify(auth)));
        encrypting=task;const settled=()=>{if(encrypting===task)encrypting=null;};task.then(settled,settled);
        const encrypted=this.encrypted(await this.waitStorage(task));
        if(this.loginWindow!==win||win.isDestroyed()||!this.authCurrent(generation,authIdentity,settingsIdentity)||this.targetLink()!==parsed.url)return;
        this.writeAuth(encrypted,fs.existsSync(this.authFile)?fs.readFileSync(this.authFile):null,authIdentity,settingsIdentity);
        this.auth=auth;this.reason='官方登录已保存，点击“连接官方远控”继续。';
        clearInterval(timer);this.loginWindow=null;win.destroy();
      }catch(error){if(this.loginWindow===win&&!win.isDestroyed()&&this.authCurrent(generation,authIdentity,settingsIdentity))this.reason=error.publicMessage||'官方登录尚未保存，请检查账号或重新登录。';}
      finally{capturing=false;}
    };
    const timer=setInterval(capture,1200);timer.unref();
    win.on('closed',()=>{clearInterval(timer);for(const popup of [...popups])if(!popup.isDestroyed())popup.destroy();if(this.loginWindow===win||!this.loginWindow){this.pendingAuthorization=null;}if(this.loginWindow===win){this.loginWindow=null;this.authGeneration++;}});
    win.webContents.on('did-finish-load',capture);
    await win.loadURL(parsed.url);return this.status();
  }
  async forget(){this.authGeneration++;this.loginWindow?.destroy();this.loginWindow=null;await this.disconnect();this.auth=null;for(const file of [this.authFile,this.legacyAuthFile])try{fs.unlinkSync(file);}catch(error){if(error.code!=='ENOENT')throw error;}this.legacyLoginSaved=false;return this.status();}
  dispose(){this.disposed=true;this.authGeneration++;this.serviceAvailable=false;this.disconnect();this.loginWindow?.destroy();this.stopServer?.();}
}
module.exports={OfficialRemote};
