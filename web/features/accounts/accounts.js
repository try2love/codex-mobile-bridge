/* Shared account selector; enrollment controls exist only in the desktop host. */
function resetCardState(value, now){
  const stale=!!value.stale||value.state==='error';
  const resetToday=!!value.lastResetAt&&new Date(value.lastResetAt*1000).toDateString()===now.toDateString();
  const status=stale?'delayed':['idle','loading'].includes(value.state)?'loading':value.officialSignal?'signal':'quiet';
  return {stale,resetToday,status,showAnnouncement:!stale&&!!value.officialSignal,
    showForecast:!stale&&value.state==='ready'&&!resetToday&&!value.officialSignal};
}
class AccountsPanel {
  constructor({root,read,request,desktop=false,toolbar=null,readOnly=false,onChanged=()=>{},onUpdate=()=>{}}){
    Object.assign(this,{root,read,request,desktop,toolbar,readOnly,onChanged,onUpdate});this.value=null;this.busy=false;this.timer=null;this.generation=0;this.visit=0;this.readFailed=false;this.resumePending=false;this.operationError=false;this.expandedModels=new Set();this.modelBoxes=new Map();this.expandedSubscriptions=new Set();
    this.build();
    this.lastVisible=this.visible();
    document.addEventListener('visibilitychange',()=>this.resume());
    if(this.desktop)document.addEventListener('bridge-client-config',()=>this.resume());
    this.root.closest('dialog')?.addEventListener('close',()=>this.resume());
  }
  attachApiAccess(panel,details){
    this.cliAccess=panel;this.apiDetails=details;this.management.insertBefore(details,this.tools);
  }
  text(value){return typeof BridgeI18n==='undefined'?value:BridgeI18n.t(value);}
  node(tag,text,className){const n=document.createElement(tag);if(text!==undefined){n.textContent=this.text(text);if(typeof BridgeI18n!=='undefined'&&Object.hasOwn(BridgeI18n.dictionary,text))n.setAttribute('data-i18n',text);}if(className)n.className=className;return n;}
  button(text,fn){const n=this.node('button',text);n.type='button';n.onclick=fn;return n;}
  field(label,type='text'){const wrap=this.node('label'),input=document.createElement('input');input.type=type;wrap.append(this.node('span',label),input);return {wrap,input};}
  build(){
    this.root.replaceChildren();this.root.classList.add('accounts-panel');
    const header=this.toolbar||this.node('div',undefined,'accounts-toolbar');
    if(this.desktop)header.append(this.node('h2','已保存的接入'));
    this.refreshButton=this.button('刷新',()=>this.refresh());this.refreshButton.className='accounts-refresh';header.append(this.refreshButton);if(!this.toolbar)this.root.append(header);
    this.switchHint=this.node('p','此电脑的 Codex Desktop 与 CLI 共用接入；桌面运行时切换会重启桌面应用。','account-muted');this.switchHint.hidden=this.readOnly;this.root.append(this.switchHint);
    this.status=this.node('p',undefined,'account-muted');this.status.setAttribute('role','status');
    this.managementNotice=this.node('p',undefined,'account-muted');this.root.append(this.managementNotice);
    if(this.desktop){
      const label=this.node('label','接入配置管理');this.managementMode=document.createElement('select');
      for(const [value,text] of [['auto','自动识别'],['external','由 Magpie 等外部工具管理']]){const option=this.node('option',text);option.value=value;this.managementMode.append(option);}
      this.managementMode.onchange=()=>this.perform({action:'management',mode:this.managementMode.value});label.append(this.managementMode);this.root.append(label);
    }
    this.error=this.node('p',undefined,'error');this.error.setAttribute('role','alert');
    this.globalReset=this.node('section',undefined,'accounts-global-reset');this.globalReset.hidden=true;
    this.rows=this.node('div',undefined,'accounts-list');
    this.currentReset=this.button('查看并使用重置卡',()=>this.onReset?.());this.currentReset.hidden=true;
    this.current=this.node('p',undefined,'accounts-current');this.blocked=this.node('div',undefined,'account-muted');this.root.append(this.current,this.status,this.error,this.currentReset,this.blocked,this.globalReset,this.rows);
    this.management=ApiAccessEditor.management(this.root,this.node.bind(this));this.management.hidden=this.readOnly;
    this.sshHosts=this.node('section');this.sshHosts.hidden=true;this.root.append(this.sshHosts);
    this.tools=this.node('details');this.toolsTitle=this.node('summary');this.tools.append(this.toolsTitle);
    this.reminders=this.node('div');this.updates=this.node('section');this.tools.append(this.reminders,this.updates);this.management.append(this.tools);
    if(!this.desktop){if(!this.readOnly)this.root.append(this.node('p','官方账号登录请在电脑端完成。','account-muted'));return;}
    const scanDetails=this.node('details');scanDetails.append(this.node('summary','扫描本机配置'));
    this.scanSource=this.field('Codex 配置目录');this.scanSource.input.placeholder=this.text('留空使用当前 Codex 数据目录');
    this.scanButton=this.button('扫描配置',()=>this.perform({action:'scan',source:this.scanSource.input.value}));
    this.scanRows=this.node('div');scanDetails.append(this.scanSource.wrap,this.scanButton,this.scanRows);this.management.append(scanDetails);
    this.renameForm=this.node('form');this.renameForm.hidden=true;this.renameName=this.field('名称');this.renameName.input.maxLength=80;this.renameName.input.required=true;
    this.renameSave=this.node('button','保存名称');this.renameSave.type='submit';
    this.renameForm.append(this.renameName.wrap,this.renameSave,this.button('取消编辑',()=>{this.renameForm.hidden=true;}));
    this.renameForm.onsubmit=async event=>{event.preventDefault();if(await this.perform({action:'rename',id:this.renameId,name:this.renameName.input.value}))this.renameForm.hidden=true;};
    this.root.append(this.renameForm);
    const details=this.node('details');details.append(this.node('summary','添加账号或 API'));
    this.form=this.node('form');this.name=this.field('名称');this.name.input.maxLength=80;this.name.input.required=true;
    this.kind=document.createElement('select');this.kind.setAttribute('aria-label',this.text('接入类型'));
    for(const [value,label] of [['chatgpt','官方 ChatGPT 账号'],['api','自定义 API']]){const option=this.node('option',label);option.value=value;this.kind.append(option);}
    this.url=this.field('API 地址');this.url.input.placeholder='https://api.example.com/v1';
    this.key=this.field('API Key','password');this.key.input.autocomplete='new-password';this.key.input.maxLength=8192;
    this.model=this.field('默认模型');this.model.input.maxLength=200;
    this.modelOptions=this.node('select');this.modelOptions.setAttribute('aria-label',this.text('上游模型'));this.modelOptions.hidden=true;
    this.modelOptions.onchange=()=>{if(this.modelOptions.value)this.model.input.value=this.modelOptions.value;};
    this.modelsButton=this.button('获取上游模型',()=>ApiAccessEditor.discover(this.apiEditor));
    this.modelMessage=this.node('p',undefined,'account-muted');
    this.submit=this.node('button','继续');this.submit.type='submit';
    const panel=this,asField=value=>({label:value.wrap,input:value.input});
    this.apiEditor={t:this.text.bind(this),node:this.node.bind(this),get busy(){return panel.busy;},draftRevision:0,draftModelMode:'legacy',draftManualToggle:false,
      model:asField(this.model),baseUrl:asField(this.url),key:asField(this.key),save:this.submit,modelSelect:this.modelOptions,modelCaption:this.model.wrap,modelFeedback:this.modelMessage,fetchModels:this.modelsButton,manualModel:this.button('手动填写',()=>{}),request:payload=>this.request(payload),
      draftModelContext:()=>JSON.stringify([this.generation,this.importId,this.editId,this.kind.value,this.url.input.value,this.key.input.value]),
      draftModelRequest:()=>this.importId?{action:'models',candidateId:this.importId}:{action:'models',id:this.editId,baseUrl:this.url.input.value,apiKey:this.key.input.value},
      onDraftStart:()=>{this.operationError=false;this.error.textContent='';},onDraftError:error=>{this.operationError=true;this.error.textContent=this.text(String(error.message).replace(/^Error invoking remote method '[^']+': (?:Error: )?/,''));},
      draftModelSuccess:'模型列表不保证支持 Responses API，请选择可用于对话的模型。'};
    const clearModels=()=>ApiAccessEditor.clear(this.apiEditor);
    this.url.input.oninput=clearModels;this.key.input.oninput=clearModels;this.clearModels=clearModels;
    const fields=()=>{this.modelsButton.hidden=this.kind.value!=='api';if(this.kind.value!=='api')clearModels();for(const field of [this.url,this.key,this.model])field.wrap.hidden=this.kind.value!=='api';};this.kind.onchange=fields;fields();
    this.cancelEdit=this.button('取消编辑',()=>{this.resetForm();fields();});
    this.form.append(this.name.wrap,this.kind,this.url.wrap,this.key.wrap,this.model.wrap,this.modelsButton,this.modelOptions,this.modelMessage,this.submit,this.cancelEdit);
    this.form.onsubmit=async event=>{
      event.preventDefault();const payload={name:this.name.input.value};
      if(this.importId)Object.assign(payload,{action:'import',candidateId:this.importId,model:this.model.input.value});
      else if(this.kind.value==='api')Object.assign(payload,{action:'addApi',id:this.editId,baseUrl:this.url.input.value,apiKey:this.key.input.value,model:this.model.input.value});
      else Object.assign(payload,{action:'login',replaceId:this.editId});
      if(await this.perform(payload)){this.resetForm();fields();}
    };
    details.append(this.form,this.node('p','账号凭据仅保存在此电脑的私有目录中。自定义 API 需要兼容 Responses API。','account-muted'));
    this.loginBox=this.node('div');details.append(this.loginBox);this.management.append(details);this.addDetails=details;
    const settings=this.node('details');settings.append(this.node('summary','桌面程序与恢复'));
    this.executable=this.field('桌面程序路径');this.executable.input.placeholder=this.text('可选择 .app 应用程序包或桌面可执行文件');
    this.scanDesktop=this.button('扫描桌面程序',()=>this.perform({action:'scanDesktop'}));
    this.desktopSettings=settings;settings.append(this.executable.wrap,this.scanDesktop,this.button('保存程序路径',()=>this.perform({action:'configure',desktopExecutable:this.executable.input.value})));
    this.recover=this.button('恢复原接入',()=>{if(window.confirm(this.text('确认恢复原接入并重启 Codex 桌面应用？')))this.perform({action:'recover',confirmed:true});});
    settings.append(this.recover);this.management.append(settings);
  }
  resetForm(){this.editId=null;this.importId=null;this.form.reset();this.key.input.value='';this.kind.disabled=false;this.url.input.disabled=false;this.key.input.disabled=false;this.clearModels();}
  importCandidate(row){
    if(row.kind==='chatgpt'){this.perform({action:'import',candidateId:row.id});return;}
    this.resetForm();this.importId=row.id;this.addDetails.open=true;this.name.input.value=row.name;this.kind.value='api';this.kind.disabled=true;this.kind.onchange();
    this.url.input.value=row.baseUrl;this.model.input.value=row.model||'';this.url.input.disabled=true;this.key.input.disabled=true;this.model.input.focus();
  }
  async perform(payload){
    if(this.busy)return false;const focused=[...this.modelBoxes.values()].find(box=>box.refresh===document.activeElement);clearTimeout(this.timer);this.generation++;this.loading=false;this.busy=true;this.operationError=false;this.error.textContent='';this.render();const generation=this.generation;
    try{const value=await this.request(payload);if(generation===this.generation){this.accept(value);return true;}return false;}
    catch(error){if(generation===this.generation){this.operationError=true;this.error.textContent=this.text(String(error.message).replace(/^Error invoking remote method '[^']+': (?:Error: )?/,''));}return false;}
    finally{if(generation===this.generation){this.busy=false;this.render();if(this.resumePending&&this.visible()){this.resumePending=false;this.refresh();}else this.schedule();if(focused?.refresh.isConnected&&(document.activeElement===document.body||document.activeElement===focused.refresh)&&!focused.touching)focused.refresh.focus({preventScroll:true});}}
  }
  async refresh(forceReset=false){
    if(this.readOnly&&!this.visible())return;
    if(this.visible()!==this.lastVisible)return this.resume();
    clearTimeout(this.timer);if(this.loading||this.busy)return;this.loading=true;const generation=this.generation,visit=this.visit;
    try{const value=await this.read(forceReset);if(generation===this.generation&&visit===this.visit){this.readFailed=false;if(forceReset)this.finishGlobalResetRefresh(value.globalReset,true);if(!this.operationError)this.error.textContent='';this.accept(value);await this.loadUsage(generation);}}
    catch(error){if(generation===this.generation&&visit===this.visit){this.readFailed=true;if(this.globalResetRefreshIntent){this.globalResetRefreshIntent=null;this.notify(this.text('连接失败，请稍后重试'));}this.error.textContent=this.text(String(error.message).replace(/^Error invoking remote method '[^']+': (?:Error: )?/,''));}}
    finally{if(generation===this.generation){this.loading=false;this.render();if(this.resumePending&&this.visible()){this.resumePending=false;this.refresh();}else this.schedule();}}
  }
  schedule(){
    clearTimeout(this.timer);this.timer=null;if(!this.visible())return;
    const value=this.value;if(!value&&!this.readFailed)return;
    const phase=value?.switch?.phase||'idle',enrollment=value?.enrollment?.phase,update=value?.desktopUpdate?.state;
    const pending=value?.sshHosts?.some(host=>host.state==='checking')||!['idle','complete','restored','failed','interrupted'].includes(phase)||['starting','waiting'].includes(enrollment)||['checking','requesting','needsDesktop'].includes(update)||value?.current?.status==='checking'||['idle','loading'].includes(value?.globalReset?.state)||value?.globalReset?.refresh?.checking||this.globalResetRefreshIntent||this.accountRows().some(row=>row.details?.usage?.status==='loading'||row.details?.models?.status==='loading');
    const sshRetry=Math.min(300000,...(value?.sshHosts||[]).filter(host=>host.state==='offline'&&host.nextRetryAt).map(host=>Math.max(5000,host.nextRetryAt*1000-Date.now()+1000)));
    this.timer=setTimeout(()=>{if(this.visible())return this.refresh();},this.readFailed?10000:pending?2000:sshRetry);
  }
  visible(){const dialog=this.root.closest('dialog');return !this.root.closest('[hidden],.client-config-hidden')&&!document.hidden&&(!dialog||dialog.open);}
  resume(){
    const visible=this.visible();if(visible===this.lastVisible)return;
    this.lastVisible=visible;this.visit++;clearTimeout(this.timer);this.timer=null;
    if(!visible){this.resumePending=false;this.globalResetRefreshIntent=null;clearTimeout(this.resetMidnightTimer);this.resetMidnightTimer=null;}
    if(visible){if(this.loading||this.busy)this.resumePending=true;else return this.refresh();}
  }
  accountRows(){const login=this.value?.loginAccount,rows=this.value?.accounts||[];return login?[login,...rows.filter(row=>row.id!==login.id)]:rows;}
  async loadUsage(generation){
    if(!this.visible())return;
    if(!['idle','complete','restored','failed'].includes(this.value?.switch?.phase||'idle'))return;
    const visit=this.visit,current=()=>generation===this.generation&&!this.busy&&visit===this.visit&&this.visible();
    for(const row of this.accountRows()){
      if(!current())return;
      const usage=row.details?.usage;
      if(row.kind!=='chatgpt'||usage?.status==='loading'||(usage&&Date.now()/1000-(usage.checkedAt||0)<300))continue;
      const value=await this.request({action:'details',id:row.id,section:'usage'});
      if(!current())return;
      this.accept(value);
    }
  }
  relative(minutes){const days=Math.floor(minutes/1440),hours=Math.floor(minutes%1440/60),rest=minutes%60;return [days?days+this.text(' 天'):'',hours?hours+this.text(' 小时'):'',rest?rest+this.text(' 分钟'):''].filter(Boolean).join(' ');}
  resetTime(stamp){const minutes=Math.ceil((stamp*1000-Date.now())/60000);return (minutes>0?this.relative(minutes)+this.text('后恢复'):this.text('等待刷新'))+' · '+new Date(stamp*1000).toLocaleString(BridgeI18n.locale());}
  shortTime(stamp,timeOnly=false){
    const date=new Date(stamp*1000),now=new Date(Date.now()),options={hour:'2-digit',minute:'2-digit'};
    if(!timeOnly||date.toDateString()!==now.toDateString())Object.assign(options,{month:'short',day:'numeric',...(date.getFullYear()!==now.getFullYear()?{year:'numeric'}:{})});
    return date.toLocaleString(BridgeI18n.locale(),options);
  }
  accountUsage(card,row){
    if(row.kind==='chatgpt'){
      const usage=row.details?.usage,box=this.node('div',undefined,'accounts-usage');
      if(usage?.status==='loading'&&!usage.limits)box.append(this.node('span','查询中…','account-query'));
      else if(!usage)box.append(this.node('span','正在读取额度…','account-muted'));
      else if(usage.status==='error')box.append(this.node('span',usage.error,'account-muted'));
      if(usage?.limits){
        for(const bucket of usage.limits||[])for(const window of bucket.windows||[]){
          const line=this.node('div',undefined,'accounts-window'),minutes=window.windowDurationMins;
          const label=minutes?minutes%1440===0?minutes/1440+this.text(' 天'):minutes%60===0?minutes/60+this.text(' 小时'):minutes+this.text(' 分钟'):this.text('额度窗口');
          line.append(this.node('span',bucket.name+' · '+label),this.node('strong',window.remainingPercent==null?this.text('暂未提供'):this.text('剩余 ')+Math.round(window.remainingPercent*10)/10+'%'));
          if(window.remainingPercent!=null){const progress=this.node('progress');progress.max=100;progress.value=window.remainingPercent;progress.setAttribute('aria-label',this.text('剩余额度'));line.append(progress);}
          if(window.resetsAt){const reset=this.node('small',undefined,'account-reset-time');reset.textContent=this.resetTime(window.resetsAt);line.append(reset);}box.append(line);
        }
        if(!usage.limits?.length)box.append(this.node('span','暂未提供额度信息','account-muted'));
        for(const bucket of usage.limits||[])if(bucket.credits){const credit=bucket.credits;if(credit.unlimited||credit.balance!=null&&String(credit.balance).trim()!=='')box.append(this.node('p',bucket.name+' · Credits: '+(credit.unlimited?this.text('不限量'):credit.balance),'account-muted'));}
        if(usage.status==='error')box.append(this.node('small','保留上次结果，数据尚未更新','account-muted'));
      }
      card.append(box);
    }
  }
  accountSubscription(card,row){
    const usage=row.details?.usage;if(!usage?.limits)return;
    const period=usage.subscription?.periodEndsAt,online=usage.subscription?.source==='online',hasDate=Number.isFinite(period)&&(online||period>Date.now()/1000);
    const date=hasDate?new Date(period*1000):null,fullDate=date?.toLocaleString(BridgeI18n.locale());
    const shortDate=date?.toLocaleDateString(BridgeI18n.locale(),{month:'short',day:'numeric',...(date.getFullYear()!==new Date(Date.now()).getFullYear()?{year:'numeric'}:{})});
    const label=hasDate?this.text(online?'订阅周期至 ':'登录记录中的订阅日期（非实时）：')+shortDate:this.text('订阅有效期暂未确认');
    const source=this.text(online?'订阅日期来自在线查询，续订与扣费状态请以 ChatGPT 订阅页面为准。':'登录记录可能滞后，请以 ChatGPT 订阅页面为准。');
    const note=this.node('p',(hasDate?this.text(online?'订阅周期截至：':'登录记录中的订阅日期（非实时）：')+fullDate+' · ':'')+source,'account-muted account-subscription-note');note.hidden=!this.expandedSubscriptions.has(row.id);
    const info=this.button(label,()=>{note.hidden=!note.hidden;if(note.hidden)this.expandedSubscriptions.delete(row.id);else this.expandedSubscriptions.add(row.id);info.setAttribute('aria-expanded',String(!note.hidden));});
    info.className='account-subscription-info';info.title=note.textContent;info.setAttribute('aria-label',this.text('订阅日期说明'));info.setAttribute('aria-expanded',String(!note.hidden));
    const subscription=this.node('div',undefined,'account-subscription');subscription.append(info);card.append(subscription,note);
    if(usage.subscription?.error)card.append(this.node('p',usage.subscription.error,'account-muted'));
  }
  accountReset(card,row,switching){
    const credits=row.details?.usage?.resetCredits;
    const summary=this.node('div',undefined,'accounts-reset-summary'),info=this.node('div',undefined,'accounts-reset-info'),count=credits?.availableCount;
    info.append(this.node('div',this.text('重置卡')+' · '+(count==null?this.text('暂未提供'):count+this.text(' 张可用')),'accounts-reset-count'));
    const expiry=credits?.credits?.filter(c=>c.status==='available'&&c.resetType==='codexRateLimits'&&Number.isFinite(c.expiresAt)&&c.expiresAt>Date.now()/1000).map(c=>c.expiresAt).sort((a,b)=>a-b)[0];
    if(expiry){const time=this.node('time',this.shortTime(expiry));time.title=new Date(expiry*1000).toLocaleString(BridgeI18n.locale());time.dateTime=new Date(expiry*1000).toISOString();const line=this.node('div',undefined,'accounts-reset-expiry account-muted');line.append(this.node('span','最近到期：'),time);info.append(line);}
    else if(count>0&&!this.readOnly)info.append(this.node('div','未提供到期时间','accounts-reset-expiry account-muted'));
    summary.append(info);
    if(this.readOnly){card.append(summary);return;}
    const reset=this.button('使用重置卡',()=>this.openReset(row));reset.className='accounts-reset';reset.disabled=this.busy||switching;reset.setAttribute('aria-label',this.text('查看并使用所选账号的重置卡'));summary.append(reset);
    card.append(summary);
  }
  async openReset(row){
    if(this.resetDialog?.open)return;
    const dialog=this.node('dialog',undefined,'picker account-reset-dialog'),heading=this.node('h2',this.text('重置卡')+' · '+row.name);
    const close=this.button('关闭',()=>dialog.close()),content=this.node('div'),button=this.node('button');
    const header=this.node('div',undefined,'picker-head');header.append(heading,close);dialog.append(header,content);
    const panel=new AccountPanel({root:content,button,resetOnly:true,
      read:refresh=>this.request({action:'account',id:row.id,operation:'read',refresh}),
      consume:value=>this.request({action:'account',id:row.id,operation:'consume',...value}),
      onConsumed:()=>this.perform({action:'details',id:row.id,section:'usage',refresh:true}),
      visible:()=>dialog.open&&this.visible()});
    this.resetDialog=dialog;this.resetPanel=panel;this.resetAccountId=row.id;this.resetAccountKey=row.accountKey;
    dialog.onclose=()=>{panel.clear();dialog.remove();this.resetDialog=null;this.resetPanel=null;this.resetAccountId=null;this.resetAccountKey=null;};
    document.body.append(dialog);dialog.showModal();
    const usage=row.details?.usage;
    // List snapshots display inventory; the account read supplies the identity required to use it.
    if(usage?.resetCredits)panel.accept({visible:true,email:row.email,limits:[],resetCredits:usage.resetCredits,updatedAt:usage.updatedAt,canReset:false});
    await panel.refresh(false);
  }
  accountActions(card,row,footer){
    if(this.readOnly&&!this.value?.capabilities?.models)return;
    const models=row.details?.models,opened=this.expandedModels.has(row.id);
    {const show=this.button(opened?'收起模型':'查看可用模型',()=>{if(opened){this.expandedModels.delete(row.id);this.render();}else{this.expandedModels.add(row.id);this.perform({action:'details',id:row.id,section:'models'});}});show.disabled=this.busy;footer.append(show);}
    if(opened){const identity=row.accountKey||JSON.stringify([row.kind,row.baseUrl,row.model,row.email]);let box=this.modelBoxes.get(row.id);
      if(box?.identity!==identity){this.modelBoxes.delete(row.id);box=null;}
      if(!box){box=this.node('div',undefined,'accounts-models');box.identity=identity;box.ontouchstart=()=>{box.touching=true;};box.ontouchend=box.ontouchcancel=()=>{box.touching=false;};box.controls=this.node('div',undefined,'accounts-model-controls');box.refresh=this.button('刷新模型',()=>this.perform({action:'details',id:row.id,section:'models',refresh:true}));box.updated=this.node('small',undefined,'account-muted');box.content=this.node('div');box.controls.append(box.refresh,box.updated);box.append(box.controls,box.content);this.modelBoxes.set(row.id,box);}
      box.refresh.disabled=this.busy||models?.status==='loading';
      const updated=models?.cache?.updatedAt;box.updated.hidden=!(Number.isFinite(updated)&&updated>0);box.updated.textContent=box.updated.hidden?'':this.text('更新于：')+this.shortTime(updated);
      const key=JSON.stringify([models?.status,models?.error,models?.models,BridgeI18n.locale()]);
      if(box.modelKey!==key){box.modelKey=key;box.content.replaceChildren();
        if((!models||models.status==='loading')&&!models?.models?.length)box.content.append(this.node('p','正在读取模型…'));
        if(models?.status==='error')box.content.append(this.node('p',models.error,'error'));
        if(models?.models?.length)for(const model of models.models){const line=this.node('div');line.textContent=!model.name||model.name===model.id?model.id:model.name+' · '+model.id;if(model.efforts?.length)line.append(this.node('small',' · '+model.efforts.map(effort=>this.text(effort)).join(' / '),'account-muted'));box.content.append(line);}
        else if(models?.status==='ready')box.content.append(this.node('p','暂未提供可用模型'));
      }if(!box.parentNode)card.append(box);
    }
  }
  accountUsageControls(row){
    const controls=this.node('div',undefined,'accounts-usage-controls'),usage=row.details?.usage;
    const refresh=this.button('',()=>this.perform({action:'details',id:row.id,section:'usage',refresh:true}));refresh.className='accounts-usage-refresh';
    refresh.innerHTML='<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M20 12a8 8 0 1 1-2.34-5.66L20 8M20 3v5h-5"/></svg>';
    refresh.title=this.text('查看剩余额度');refresh.setAttribute('aria-label',refresh.title);
    refresh.disabled=this.busy||usage?.status==='loading';
    if(usage?.updatedAt){const updated=this.node('small',this.text('{time} 更新').replace('{time}',this.shortTime(usage.updatedAt,true))+(usage.status==='loading'?' · '+this.text('查询中…'):''),'account-updated account-muted');updated.title=new Date(usage.updatedAt*1000).toLocaleString(BridgeI18n.locale());controls.append(updated);}
    controls.append(refresh);return controls;
  }
  accept(value){const old=this.value;if(!this.desktop&&old?.canSwitch===false&&value.canSwitch===undefined)value={...value,canSwitch:false};this.value=value;if(!value.resetDynamicEnabled)this.globalResetRefreshIntent=null;this.finishGlobalResetRefresh(value.globalReset);if(old&&(old.activeId!==value.activeId||old.current?.kind!==value.current?.kind||old.current?.name!==value.current?.name||old.switch?.phase!==value.switch?.phase))this.onChanged(value);this.onUpdate(value);this.render();}
  clear(){clearTimeout(this.resetMidnightTimer);this.resetMidnightTimer=null;this.globalResetRefreshIntent=null;this.globalResetMessage='';this.globalResetKey=null;this.globalReset.hidden=true;this.resetDialog?.close();clearTimeout(this.timer);this.generation++;this.clearModels?.();this.value=null;this.loading=false;this.busy=false;this.readFailed=false;this.resumePending=false;this.operationError=false;this.modelBoxes.clear();this.error.textContent='';this.rows.replaceChildren();this.sshHosts.replaceChildren();this.sshHosts.hidden=true;}
  requestId(){
    if(typeof crypto.randomUUID==='function')return crypto.randomUUID();
    const bytes=crypto.getRandomValues(new Uint8Array(16));bytes[6]=(bytes[6]&15)|64;bytes[8]=(bytes[8]&63)|128;
    const h=[...bytes].map(x=>x.toString(16).padStart(2,'0')).join('');return h.slice(0,8)+'-'+h.slice(8,12)+'-'+h.slice(12,16)+'-'+h.slice(16,20)+'-'+h.slice(20);
  }
  async choose(row){
    if(!window.confirm(this.text(this.value?.scope==='profile'?'确认此配置中的所有任务已结束，并切换其 Codex 账号？此配置的空闲运行服务会正常退出。':'确认所有任务（包括未在网页显示的任务）已结束，并切换此电脑的 Codex 接入？桌面运行时会重启。')+'\n'+row.name))return;
    try{await this.perform({action:'switch',id:row.id,requestId:this.requestId(),confirmed:true,tasksConfirmed:true,
      syncLocalCli:true});this.refresh();}
    catch(error){this.operationError=true;this.error.textContent=this.text('无法创建切换请求，请刷新页面后重试');}
  }
  renderTools(){
    const value=this.value;if(!value)return;
    this.reminders.replaceChildren();
    this.reminders.append(this.node('p','提醒通过电脑端已配置的 Bark、ntfy 或 PushPlus 发送，电脑与网关需要保持运行。','account-muted'));
    const labels={globalReset:'重置动态提醒',lowQuota:'额度不足 10% 时提醒',quotaReset:'确认额度恢复后提醒',resetExpiry:'重置卡到期前 24 小时提醒',subscriptionExpiry:'订阅周期截止前 7／3／1 天提醒',desktopUpdate:'Codex Desktop 新版本提醒'};
    for(const [key,label] of Object.entries(labels)){if(value.scope==='profile'&&key==='desktopUpdate')continue;
      const wrap=this.node('label',undefined,'account-reminder'),input=document.createElement('input');input.type='checkbox';input.checked=!!value.reminders?.[key];input.disabled=this.busy;
      input.onchange=()=>this.perform({action:'reminders',preferences:{[key]:input.checked}});wrap.append(input,this.node('span',label));this.reminders.append(wrap);
    }
    if(value.reminderError)this.reminders.append(this.node('p',value.reminderError,'error'));
    const update=value.desktopUpdate||{state:'idle'},labelsUpdate={idle:'尚未检查',checking:'正在检查官方更新渠道…',available:'官方渠道有新版本',checked:'本次未发现更高的公开版本',unsupported:'需要在电脑端检查',error:'更新检查失败',requesting:'正在打开原生更新器…',needsDesktop:'等待电脑端确认'};
    this.updates.replaceChildren(this.node('h3','Codex Desktop 更新'),this.node('p',update.failureReason?'未能打开更新器':labelsUpdate[update.state]||update.state));
    this.updates.append(this.node('p','可远程查看新版本；下载和安装仍需在电脑端确认。','account-muted'));
    if(update.currentVersion)this.updates.append(this.node('p',this.text('当前版本：')+update.currentVersion+(update.targetVersion?' → '+update.targetVersion:''),'account-muted'));
    if(update.installation?.state==='verified'){const installed=this.node('p',undefined,'account-muted');installed.textContent=this.text('已核验电脑端安装版本已更新')+' · '+update.installation.previousBuild+' → '+update.installation.currentBuild;this.updates.append(installed);}
    if(update.message)this.updates.append(this.node('p',update.message,'account-muted'));
    if(update.checkedAt)this.updates.append(this.node('small',this.text('检查于：')+new Date(update.checkedAt*1000).toLocaleString(BridgeI18n.locale()),'account-muted'));
    const checking=['checking','requesting'].includes(update.state),denied=!this.desktop&&(value.canManageDesktop??value.canSwitch)===false;
    const check=this.button('查看 Codex 新版本',()=>this.perform({action:'checkDesktopUpdate'}));check.disabled=this.busy||checking||denied;this.updates.append(check);
    if(update.canRequest&&update.currentBuild){const request=this.button('打开电脑端更新窗口',()=>{
      if(window.confirm(this.text('确认所有桌面任务（包括未在网页显示的任务）已结束，并在电脑上打开 Codex 的“检查更新”界面？后续检查、下载与安装由 Codex 自行处理。')))
        this.perform({action:'requestDesktopUpdate',requestId:this.requestId(),currentBuild:update.currentBuild,confirmed:true,tasksConfirmed:true});
    });request.disabled=this.busy||checking||denied;this.updates.append(request);}
  }
  reconcileRows(cards){
    const previous=new Map([...this.rows.children].filter(card=>card.accountRowId).map(card=>[card.accountRowId,card]));
    const next=cards.map(card=>{
      const old=previous.get(card.accountRowId);if(!old)return card;
      const box=this.modelBoxes.get(card.accountRowId),keep=box?.parentNode===old?box:null;
      for(const child of [...old.children])if(child!==keep)child.remove();
      for(const child of [...card.children])old.insertBefore(child,keep);
      if(keep&&!this.expandedModels.has(card.accountRowId))keep.remove();
      old.className=card.className;return old;
    });
    for(const child of [...this.rows.children])if(!next.includes(child))child.remove();
    next.forEach((card,index)=>{if(this.rows.children[index]!==card)this.rows.insertBefore(card,this.rows.children[index]||null);});
    const identities=new Map(this.accountRows().map(row=>[row.id,row.accountKey||JSON.stringify([row.kind,row.baseUrl,row.model,row.email])]));
    for(const [id,box] of this.modelBoxes)if(identities.get(id)!==box.identity)this.modelBoxes.delete(id);
  }
  refreshIcon(){return '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M20 12a8 8 0 1 1-2.34-5.66L20 8M20 3v5h-5"/></svg>';}
  notify(message){this.globalResetMessage=message;this.globalResetKey=null;this.renderGlobalReset();}
  refreshGlobalReset(){
    if(this.busy||this.loading||this.globalResetRefreshIntent||this.value?.globalReset?.refresh?.checking)return;
    this.globalResetMessage='';this.globalResetRefreshIntent={baseline:this.value?.globalReset?.refresh?.id||0,id:null,instanceId:this.value?.globalReset?.refresh?.instanceId};
    this.globalResetKey=null;this.renderGlobalReset();return this.refresh(true);
  }
  finishGlobalResetRefresh(value,acknowledged=false){
    const intent=this.globalResetRefreshIntent;if(!intent||(!acknowledged&&intent.id===null))return;
    const refresh=value?.refresh,now=Date.now()/1000;
    if(refresh?.instanceId&&intent.instanceId&&refresh.instanceId!==intent.instanceId){
      this.globalResetRefreshIntent=null;if(this.visible())this.notify(this.text('网关已重连，请重新刷新'));return;
    }
    if(acknowledged&&refresh?.instanceId)intent.instanceId=refresh.instanceId;
    if(!acknowledged&&(!refresh||refresh.id<intent.id))return;
    if(refresh?.checking){if(acknowledged)intent.id=refresh.id;return;}
    let message;
    if(refresh&&(refresh.id>intent.baseline||(intent.id!==null&&refresh.id>=intent.id))&&value.state==='ready'){
      message=this.text(refresh.changed?'已更新':'已检查，暂无新动态');
    }else if(refresh?.retryAt>now){
      message=this.text('暂时无法更新，{minutes} 分钟后可重试').replace('{minutes}',Math.ceil((refresh.retryAt-now)/60));
    }else if(refresh?.cooldownUntil>now){
      message=this.text('刚刚检查过，{seconds} 秒后可刷新').replace('{seconds}',Math.ceil(refresh.cooldownUntil-now));
    }else message=this.text(refresh?'查询失败，请稍后重试':'已读取缓存');
    this.globalResetRefreshIntent=null;if(this.visible())this.notify(message);
  }


  renderGlobalReset(){
    const value=this.value?.globalReset;
    this.globalReset.hidden=!this.value?.resetDynamicEnabled;if(this.globalReset.hidden||!value){clearTimeout(this.resetMidnightTimer);this.resetMidnightTimer=null;return;}
    const key=JSON.stringify([value,this.value.reminders,this.busy,this.loading,!!this.refreshingGlobalReset||!!this.globalResetRefreshIntent,BridgeI18n.language(),Math.floor(Date.now()/60000)]);
    if(key===this.globalResetKey)return;this.globalResetKey=key;
    this.globalReset.replaceChildren();
    const header=this.node('div',undefined,'accounts-global-heading');
    const reminderEnabled=!!(this.value.reminders?.globalReset||this.value.reminders?.globalResetSignal);
    const reminder=this.button('',()=>this.perform({action:'reminders',preferences:{globalReset:!reminderEnabled,globalResetSignal:!reminderEnabled}}));
    reminder.className='accounts-global-reminder';reminder.disabled=this.busy;reminder.title=this.text(reminderEnabled?'关闭重置动态提醒':'开启重置动态提醒');reminder.setAttribute('aria-label',reminder.title);reminder.setAttribute('aria-pressed',String(reminderEnabled));reminder.setAttribute('data-enabled',String(reminderEnabled));
    reminder.innerHTML='<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path class="accounts-global-bell" d="M18 8a6 6 0 0 0-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9Z"/><path d="M10 21h4"/></svg>';
    reminder.append(this.node('span',reminderEnabled?'提醒已开启':'提醒已关闭'));
    header.append(this.node('h3','Tibo 重置动态'),reminder);this.globalReset.append(header,this.node('p','第三方统计与预测，仅供参考；个人额度以账号卡片为准。','account-muted'));
    const today=new Date(Date.now()),now=today.getTime()/1000;
    const {stale,resetToday,status,showAnnouncement,showForecast}=resetCardState(value,today);
    clearTimeout(this.resetMidnightTimer);this.resetMidnightTimer=null;
    if(resetToday&&this.visible()){
      const midnight=new Date(today);midnight.setHours(24,0,0,0);
      this.resetMidnightTimer=setTimeout(()=>{this.resetMidnightTimer=null;this.globalResetKey=null;this.renderGlobalReset();},midnight.getTime()-today.getTime());
    }
    if(value.lastResetAt){
      const label=resetToday?'今日已全局重置':'上次全局重置';
      const time=resetToday?this.shortTime(value.lastResetAt,true):this.globalTime(value.lastResetAt);
      const last=this.node('p',this.text(label)+' · '+time,'accounts-global-state');
      last.setAttribute('data-state',resetToday&&!stale?'confirmed':'quiet');this.globalReset.append(last);
    }
    let text='',tone='quiet';
    if(status==='delayed'){text=value.updatedAt?'数据延迟 · 等待更新':'重置动态暂时加载失败';tone='delayed';}
    else if(status==='loading')text='正在读取重置动态…';
    else if(status==='signal'){
      const end=value.officialSignal.endsAt;
      text=this.text('官方已预告')+' · '+(end>now?this.globalTime(end)+this.text('前重置'):this.text(end?'等待重置完成':'即将重置'));tone='signal';
    }
    if(text){const state=this.node('p',text,'accounts-global-state');state.setAttribute('data-state',tone);state.setAttribute('role','status');this.globalReset.append(state);}
    if(showAnnouncement)this.renderGlobalAnnouncement(value.officialSignal);
    if(showForecast){
      const odds=value.probabilities||{},row=this.node('div',undefined,'accounts-global-odds');
      for(const [key,label] of [['h24','24 小时内'],['h48','48 小时内']])if(Number.isFinite(odds[key])){
        const item=this.node('span');item.append(this.node('span',label),this.node('strong',odds[key]+'%'));row.append(item);
      }
      if(row.children.length){this.globalReset.append(row);const confidence={low:'低置信度',medium:'中置信度',high:'高置信度'}[value.confidence];this.globalReset.append(this.node('p',this.text('预测重置概率')+(confidence?' · '+this.text(confidence):''),'accounts-global-confidence'));}
    }
    if(this.globalResetMessage){const feedback=this.node('p',this.globalResetMessage,'accounts-global-feedback');feedback.setAttribute('role','status');this.globalReset.append(feedback);}
    const footer=this.node('div',undefined,'accounts-global-source'),source=this.node('a','来源 codex-reset.com ↗');source.href='https://codex-reset.com/zh/';source.target='_blank';source.rel='noopener noreferrer';
    const actions=this.node('div',undefined,'accounts-global-footer-actions');
    if(value.updatedAt){const updated=this.node('span',this.text('更新于 ')+this.shortTime(value.updatedAt,true));if(value.checkedAt)updated.title=this.text('本次检查于 ')+this.shortTime(value.checkedAt);actions.append(updated);}
    const refresh=this.button('',()=>this.refreshGlobalReset());refresh.className='accounts-global-refresh';refresh.title=this.text('刷新重置动态');refresh.setAttribute('aria-label',refresh.title);
    const refreshing=!!this.refreshingGlobalReset||!!this.globalResetRefreshIntent||!!value.refresh?.checking||value.state==='loading';refresh.disabled=this.busy||this.loading||refreshing;refresh.setAttribute('aria-busy',String(refreshing));
    refresh.innerHTML=this.refreshIcon();
    actions.append(refresh);footer.append(source,actions);this.globalReset.append(footer);
  }
  renderGlobalAnnouncement(signal){
    const original=signal.text||signal.summary,translation=signal.text&&signal.translation?.locale==='zh'&&typeof signal.translation.text==='string'?signal.translation.text:null;
    const language=BridgeI18n.language(),key=JSON.stringify([signal.at,signal.url,original,translation,language]);
    if(this.globalAnnouncementKey!==key){this.globalAnnouncementKey=key;this.globalAnnouncementExpanded=false;this.globalAnnouncementOriginal=language!=='zh';}
    const translated=!!translation&&!this.globalAnnouncementOriginal,content=translated?translation:original;
    if(!content&&!signal.url)return;
    const section=this.node('div',undefined,'accounts-global-announcement');
    if(content){
      const meta=this.node('div',undefined,'accounts-global-announcement-meta');meta.append(this.node('span',translated?'机器翻译':signal.text?'公告原文':'来源摘要'));
      if(signal.at)meta.append(this.node('span',this.text('发布于 ')+this.globalTime(signal.at)));section.append(meta);
      const body=this.node('p',undefined,'accounts-global-announcement-text');body.textContent=content;body.setAttribute('data-expanded',String(!!this.globalAnnouncementExpanded));section.append(body);
      const toggle=this.button(this.globalAnnouncementExpanded?'收起全文':'展开全文',()=>{
        this.globalAnnouncementExpanded=!this.globalAnnouncementExpanded;body.setAttribute('data-expanded',String(this.globalAnnouncementExpanded));
        toggle.textContent=this.text(this.globalAnnouncementExpanded?'收起全文':'展开全文');toggle.setAttribute('aria-expanded',String(this.globalAnnouncementExpanded));
      });toggle.className='accounts-global-expand';toggle.setAttribute('aria-expanded',String(!!this.globalAnnouncementExpanded));
      toggle.hidden=content.length<=120&&!content.includes('\n');section.append(toggle);
      window.requestAnimationFrame?.(()=>{if(body.isConnected&&!this.globalAnnouncementExpanded)toggle.hidden=body.scrollHeight<=body.clientHeight+1;});
    }
    if(translation){
      const languageToggle=this.button(translated?'查看原文':'查看译文',()=>{
        this.globalAnnouncementOriginal=!this.globalAnnouncementOriginal;this.globalAnnouncementExpanded=false;this.globalResetKey=null;this.renderGlobalReset();
      });languageToggle.className='accounts-global-language';section.append(languageToggle);
    }
    if(signal.url){
      try{const url=new URL(signal.url);if(url.protocol==='https:'&&['x.com','twitter.com'].includes(url.hostname)){
        const link=this.node('a','查看原帖 ↗');link.href=url.href;link.target='_blank';link.rel='noopener noreferrer';section.append(link);
      }}catch{}
    }
    this.globalReset.append(section);
  }
  globalTime(stamp){
    const date=new Date(stamp*1000),today=new Date(Date.now()),yesterday=new Date(Date.now());yesterday.setDate(today.getDate()-1);
    const prefix=date.toDateString()===today.toDateString()?this.text('今天 '):date.toDateString()===yesterday.toDateString()?this.text('昨天 '):'';
    return prefix+(prefix?date.toLocaleString(BridgeI18n.locale(),{hour:'2-digit',minute:'2-digit'}):this.shortTime(stamp));
  }
  renderSshHosts(){
    const hosts=this.value?.sshHosts||[];this.sshHosts.hidden=!hosts.length;this.sshHosts.replaceChildren();
    if(!hosts.length)return;
    this.sshHosts.append(this.node('h3','SSH 连接'));
    for(const host of hosts){
      const row=this.node('div',undefined,'account-bucket'),name=this.node('strong');name.textContent=host.label;
      const status=this.node('p',undefined,'account-muted'),labels={online:'可连接',offline:'连接不可用',checking:'正在重试连接…',unknown:'尚未检查'};
      status.textContent=this.text(labels[host.state]||labels.unknown);
      if(host.state==='offline'&&host.nextRetryAt)status.textContent+=' · '+this.text('下次自动重试：')+this.shortTime(host.nextRetryAt,true);
      const retry=this.button('重试 SSH 连接',()=>this.perform({action:'retrySsh',host:host.host}));retry.disabled=this.busy||host.state==='checking';
      row.append(name,status,retry);this.sshHosts.append(row);
    }
  }
  render(){
    const value=this.value;if(!value)return;
    this.renderGlobalReset();
    this.renderSshHosts();
    const external=value.management?.external===true;
    this.switchHint.hidden=external||this.readOnly;
    this.switchHint.textContent=this.text(value.scope==='profile'?'此配置的账号与默认 Codex 独立；切换仅作用于此配置。':'此电脑的 Codex Desktop 与 CLI 共用接入；桌面运行时切换会重启桌面应用。');
    this.toolsTitle.textContent=this.text(value.scope==='profile'?'账号提醒':'提醒与 Codex Desktop 更新');
    this.updates.hidden=value.scope==='profile';
    if(this.desktopSettings)this.desktopSettings.hidden=value.scope==='profile';
    if(this.recover)this.recover.hidden=value.scope==='profile';
    this.tools.hidden=value.scope==='wsl';
    this.managementNotice.textContent=[value.scopeLabel,value.scope==='profile'?value.codexHome:'',this.text(value.management?.reason||'')].filter(Boolean).join(' · ');this.managementNotice.hidden=!external&&value.scope!=='profile';
    if(this.managementMode){this.managementMode.value=value.management?.mode||'auto';this.managementMode.disabled=this.busy;}
    const phase=value.switch?.phase||'idle',labels={idle:'请选择已保存的接入',preparing:'正在准备账号',stopping:'正在退出 Codex 桌面应用',applying:'正在应用接入配置',starting:'正在启动 Codex 桌面应用',verifying:'正在核验账号与桌面连接',complete:'切换完成',restoring:'正在恢复原接入',restored:'已恢复原接入',failed:'切换未完成',interrupted:'需要在桌面端恢复原接入'};
    const switching=!['idle','complete','restored','failed'].includes(phase);
    const resetAccount=this.accountRows().find(row=>row.id===this.resetAccountId);
    if(this.resetDialog?.open&&(switching||!resetAccount||this.resetAccountKey&&this.resetAccountKey!==resetAccount.accountKey))this.resetDialog.close();
    this.status.textContent=this.text(labels[phase]||phase)+(value.switch?.error?' · '+this.text(value.switch.error):'');
    if(value.switch?.cliSync?.status==='verified')this.status.textContent+=' · '+this.text('本机 CLI 已核验');
    else if(value.switch?.cliSync?.status==='failed')this.status.textContent+=' · '+this.text('桌面已切换；本机 CLI 核验失败：')+this.text(value.switch.cliSync.error||'请检查接入');
    else if(value.switch?.cliSync?.status==='shared-config-unverified')this.status.textContent+=' · '+this.text('共享配置已变更，本机 CLI 尚未核验');
    this.status.hidden=!value.switch?.error&&(['complete','restored'].includes(phase)||(phase==='idle'&&value.current?.status==='ready'));
    const current=value.current;this.current.textContent=this.text('当前接入：')+(current?.status==='ready'?(current.kind==='signedOut'?this.text('Codex 未登录'):current.name+(current.id||current.kind==='external'?'':this.text('（未保存到列表）'))):this.text(current?.status==='checking'?'正在识别…':'暂未识别'));
    this.currentReset.hidden=this.readOnly||!this.onReset||current?.kind!=='chatgpt'||!!value.activeId;this.currentReset.disabled=this.busy||switching;
    this.blocked.replaceChildren();for(const row of value.blockers||[]){const line=this.node('p');line.textContent=this.text(({approval:'待确认',running:'运行中',queued:'排队中',unknown:'发送结果未确认'})[row.reason]||'运行中')+' · '+row.title;if(row.reason==='unknown'&&row.submissionId){
      const ignore=this.button('忽略',()=>{if(window.confirm(this.text('忽略只会移除未确认提示，不会撤回或重发消息。请先检查聊天记录。')))return this.perform({action:'ignoreSubmission',threadId:row.id,submissionId:row.submissionId,host:row.host||'local'});});
      ignore.disabled=this.busy||switching;line.append(ignore);
    }this.blocked.append(line);}
    this.refreshButton.disabled=this.loading;if(!this.readOnly)this.renderTools();const cards=[];
    if(!this.accountRows().length&&!external)cards.push(this.node('p',this.readOnly?'尚未识别当前接入。':'尚未添加账号。请在电脑端添加官方账号或自定义 API。'));
    for(const row of [...this.accountRows()].sort((a,b)=>Number(b.id===value.loginAccount?.id)-Number(a.id===value.loginAccount?.id)||Number(b.id===value.activeId)-Number(a.id===value.activeId))){
      const card=this.node('section',undefined,'account-bucket'),active=value.activeId===row.id;card.accountRowId=row.id;
      if(active)card.classList.add('is-current');
      const actions=this.node('div',undefined,'accounts-card-footer');
      const identity=this.node('div',undefined,'accounts-identity'),name=this.node('div',undefined,'accounts-name');
      const heading=this.node('h3');heading.textContent=row.name;const title=this.node('div',undefined,'accounts-name-heading');title.append(heading);
      if(row.id===value.loginAccount?.id)title.append(this.node('span','已登录','accounts-plan'));
      if(row.kind==='chatgpt'){
        identity.classList.add('accounts-official');
        if(row.details?.usage?.planType||row.planType)title.append(this.node('span',row.details?.usage?.planType||row.planType,'accounts-plan'));
      }
      name.append(title,this.node('p',row.kind==='chatgpt'?(row.email||this.text('官方 ChatGPT 账号')):(this.readOnly?(row.model||this.text('API 接入')):row.baseUrl+' · '+row.model),'account-muted'));
      if(row.kind==='chatgpt')this.accountSubscription(name,row);
      identity.append(name);this.accountUsage(identity,row);card.append(identity);
      if(row.kind==='chatgpt')this.accountReset(card,row,switching);
      if(!row.transient&&!this.readOnly){const choose=this.button(active?'当前接入':'切换到此接入',()=>this.choose(row));choose.disabled=this.busy||switching||active||external||(!this.desktop&&value.canSwitch===false);actions.append(choose);}

      if(this.cliAccess&&row.kind==='api'&&!row.transient){
        if(!active){const edit=this.button('修改',()=>{this.apiDetails.open=true;this.cliAccess.edit('codex',row);});edit.disabled=this.busy||switching||this.cliAccess.busy;actions.append(edit);}
        for(const [label,action] of [['查看上游模型','models'],['测试模型接口','test']]){const probe=this.button(label,()=>{this.apiDetails.open=true;return this.cliAccess.probe(row,action);});probe.disabled=this.busy||switching||this.cliAccess.busy;actions.append(probe);}
      }
      if(this.desktop&&!row.transient){
        const edit=this.button(row.kind==='chatgpt'?'重新登录':'修改',()=>{this.resetForm();this.editId=row.id;this.addDetails.open=true;this.name.input.value=row.name;this.kind.value=row.kind;this.kind.disabled=true;this.kind.onchange();this.url.input.value=row.baseUrl||'';this.model.input.value=row.model||'';this.key.input.value='';this.name.input.focus();});
        edit.hidden=active||row.kind==='api'&&!!this.cliAccess;edit.disabled=this.busy||switching||active;
        const rename=this.button('重命名',()=>{this.renameId=row.id;this.renameName.input.value=row.name;this.renameForm.hidden=false;this.renameName.input.focus();});rename.disabled=this.busy||switching;
        const remove=this.button('删除',()=>{if(window.confirm(this.text('删除此账号档案？')+'\n'+row.name))this.perform({action:'delete',id:row.id});});remove.hidden=active;remove.disabled=this.busy||switching||active;
        actions.append(edit,rename,remove);
      }
      card.append(actions);this.accountActions(card,row,actions);if(row.kind==='chatgpt')actions.append(this.accountUsageControls(row));cards.push(card);
    }
    if(!this.readOnly&&!this.desktop&&value.canSwitch===false&&!external)cards.push(this.node('p','免密访问不能切换账号，请在桌面端操作'));
    this.reconcileRows(cards);
    if(this.desktop){
      this.scanDesktop.disabled=this.busy||switching;this.renameSave.disabled=this.busy||switching;this.scanButton.disabled=this.busy||switching;this.modelsButton.disabled=this.busy||switching||!!this.apiEditor?.draftLoading;this.submit.disabled=this.busy||switching||!!this.apiEditor?.draftLoading;
      this.scanSource.input.placeholder=value.codexHome||this.text('留空使用当前 Codex 数据目录');this.scanRows.replaceChildren();
      if(value.discovery){const scan=value.discovery;const source=this.node('p',undefined,'account-muted');source.textContent=scan.source||'';this.scanRows.append(source);
        if(scan.notice)this.scanRows.append(this.node('p',scan.notice,'account-muted'));
        if(!scan.candidates.length)this.scanRows.append(this.node('p','没有发现可导入的账号或 API 配置。'));
        for(const row of scan.candidates){const card=this.node('section',undefined,'account-bucket'),heading=this.node('h3');heading.textContent=row.name;
          const detail=this.node('p',undefined,'account-muted');detail.textContent=row.email||[row.baseUrl,row.model].filter(Boolean).join(' · ');card.append(heading,detail);
          if(row.reason)card.append(this.node('p',row.reason,'account-muted'));
          const button=this.button(row.imported?'已导入':row.kind==='api'?'选择并导入':'导入账号',()=>this.importCandidate(row));button.disabled=this.busy||switching||row.imported||!row.canImport;
          card.append(button);this.scanRows.append(card);}
      }
      if(document.activeElement!==this.executable.input&&!this.executable.input.value)this.executable.input.value=value.desktopExecutable||'';
      this.recover.disabled=this.busy||external||phase!=='interrupted';this.submit.disabled=this.busy||switching||['starting','waiting'].includes(value.enrollment?.phase);
      this.loginBox.replaceChildren();const login=value.enrollment;
      if(login){
        this.loginBox.append(this.node('p',({starting:'正在准备登录',waiting:'请在此电脑的浏览器完成官方登录',complete:'账号已添加',failed:'登录未完成，请重新添加'})[login.phase]||''));
        if(login.phase==='waiting'&&login.authUrl){this.loginBox.append(this.button('打开官方登录',()=>this.perform({action:'openLogin'})));}
        if(['starting','waiting'].includes(login.phase))this.loginBox.append(this.button('取消登录',()=>this.perform({action:'cancelLogin'})));
      }
    }
  }
}
