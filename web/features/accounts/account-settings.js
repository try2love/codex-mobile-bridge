/* Shared notification preferences and Codex maintenance views. Reads never query account quota. */
class AccountSettingsPanel {
  constructor({remindersRoot,updatesRoot=null,read,request,requestId,desktop=false,onUpdate=()=>{},stateSync=null}){
    Object.assign(this,{remindersRoot,updatesRoot,read,request,requestId,desktop,onUpdate,stateSync});
    this.value=null;this.busy=false;this.loading=false;this.revision=0;this.error='';this.operationError=false;this.timer=null;this.signatures=new WeakMap();
    for(const root of [remindersRoot,updatesRoot])this.prepare(root);
    document.addEventListener('bridge-language',()=>this.render());
    document.addEventListener('visibilitychange',()=>{if(this.visible())this.refresh();else this.schedule();});
    this.render();
    stateSync?.subscribe(()=>{
      clearTimeout(this.timer);this.busy=stateSync.busy;
      if(this.busy){this.revision++;this.loading=false;}
      this.render();this.schedule();
    });
  }
  text(value){return BridgeI18n.t(value);}
  node(tag,text,cls){const node=document.createElement(tag);if(text!==undefined)node.textContent=this.text(text);if(cls)node.className=cls;return node;}
  button(text,fn){const button=this.node('button',text);button.type='button';button.onclick=fn;return button;}
  prepare(root){if(root){root.classList.add('account-settings');root.closest('details')?.addEventListener('toggle',()=>{if(this.visible())this.refresh();else this.schedule();});root.closest('dialog')?.addEventListener('close',()=>this.schedule());}}
  shown(root){const dialog=root?.closest('dialog');return !!root&&root.isConnected!==false&&!document.hidden&&!root.closest('[hidden],.client-config-hidden,details:not([open])')&&(!dialog||dialog.open);}
  visible(){return this.shown(this.remindersRoot)||this.shown(this.updatesRoot);}
  schedule(){
    clearTimeout(this.timer);this.timer=null;
    if(!this.busy&&!this.loading&&!this.error&&this.shown(this.updatesRoot)&&['checking','requesting'].includes(this.value?.desktopUpdate?.state))
      this.timer=setTimeout(()=>this.refresh(),2000);
  }
  mountUpdates(root){this.updatesRoot=root;this.prepare(root);this.render();return this.refresh();}
  retainAccess(value){return this.value?.canSwitch===false&&value.canSwitch===undefined?{...value,canSwitch:false}:value;}
  accept(value){
    if((this.busy&&!this.stateSync)||value===this.value)return;
    if(this.loading){this.revision++;this.loading=false;}
    if(!this.operationError)this.error='';this.value=this.retainAccess(value);this.render();
    this.schedule();
  }
  clear(){clearTimeout(this.timer);this.revision++;this.value=null;this.loading=false;this.busy=false;this.error='';this.operationError=false;this.signatures=new WeakMap();this.render();}
  async refresh(){
    clearTimeout(this.timer);if(!this.visible()||this.loading||this.busy||this.stateSync?.busy)return;
    this.loading=true;const revision=this.revision,syncRevision=this.stateSync?.revision;
    try{const value=await this.read();if(revision!==this.revision||syncRevision!==this.stateSync?.revision)return;this.error='';this.operationError=false;this.value=this.retainAccess(value);this.onUpdate(this.value);}
    catch(error){if(revision===this.revision){this.error=error.message;this.operationError=false;}}
    finally{if(revision===this.revision){this.loading=false;this.render();this.schedule();}}
  }
  async perform(value){
    if(this.busy||this.stateSync?.busy)return;const endWrite=this.stateSync?.begin();clearTimeout(this.timer);const revision=++this.revision;this.busy=true;this.loading=false;this.error='';this.operationError=false;this.render();
    try{const result=await this.request(value);if(revision===this.revision){this.value=this.retainAccess(result);this.onUpdate(this.value);}}
    catch(error){if(revision===this.revision){this.error=error.message;this.operationError=true;}}
    finally{if(revision===this.revision){this.busy=false;this.render();this.schedule();}endWrite?.();}
  }
  begin(root,value){
    if(!this.shown(root))return false;
    const key=JSON.stringify([value,this.busy,this.error,BridgeI18n.language()]);if(this.signatures.get(root)===key)return false;
    this.signatures.set(root,key);root.replaceChildren();
    if(this.error){const error=this.node('p',this.error,'error');error.setAttribute('role','alert');root.append(error,this.button('重试读取',()=>this.refresh()));}
    if(!this.value){root.append(this.node('p',this.error?'提醒与更新状态读取失败':'正在读取…','account-settings-note'));return false;}
    return true;
  }
  render(){this.renderReminders();this.renderUpdates();}
  renderReminders(){
    const root=this.remindersRoot,value=this.value;
    if(!this.begin(root,[value?.reminders,value?.reminderError]))return;
    root.append(this.node('p','提醒通过电脑端已配置的 Bark、ntfy 或 PushPlus 发送，电脑与网关需要保持运行。','account-settings-note'));
    const groups=[['账号与额度提醒',[
      ['lowQuota','额度不足','额度不足 10% 时提醒'],['quotaReset','额度恢复','确认额度恢复后提醒'],
      ['resetExpiry','重置卡到期','重置卡到期前 24 小时提醒'],['subscriptionExpiry','订阅周期','订阅周期截止前 7／3／1 天提醒']
    ]],['重置动态',[
      ['globalReset','重置动态提醒','接收官方重置预告与全局重置确认']
    ]],['客户端更新提醒',[
      ['desktopUpdate','Codex Desktop 新版本提醒','发现新版本时提醒，安装在电脑端完成']
    ]]];
    for(const [title,rows] of groups){
      const section=this.node('section',undefined,'account-settings-group');section.append(this.node('h3',title));
      for(const [key,title,description] of rows){
        const label=this.node('label',undefined,'account-settings-option'),copy=this.node('span');copy.append(this.node('strong',title),this.node('small',description));
        const input=this.node('input');input.type='checkbox';input.setAttribute('role','switch');input.checked=key==='globalReset'?!!(value.reminders?.globalReset||value.reminders?.globalResetSignal):!!value.reminders?.[key];input.disabled=this.busy;
        input.oninput=event=>event.stopPropagation();
        input.onchange=event=>{event?.stopPropagation();return this.perform({action:'reminders',preferences:key==='globalReset'?{globalReset:input.checked,globalResetSignal:input.checked}:{[key]:input.checked}});};
        label.append(copy,input);section.append(label);
      }
      root.append(section);
    }
    if(value.reminderError){const error=this.node('p',value.reminderError,'error');error.setAttribute('role','alert');root.append(error);}
  }
  renderUpdates(){
    const root=this.updatesRoot,value=this.value;
    if(!this.begin(root,[value?.desktopUpdate,value?.canSwitch]))return;
    const update=value.desktopUpdate||{state:'idle'},labels={idle:'尚未检查',checking:'正在检查官方更新渠道…',available:'官方渠道有新版本',checked:'本次未发现更高的公开版本',unsupported:'需要在电脑端检查',error:'更新检查失败',requesting:'正在打开原生更新器…',needsDesktop:'等待电脑端确认'};
    root.append(this.node('h3','Codex Desktop 更新'),this.node('p',update.failureReason?'未能打开更新器':labels[update.state]||update.state,'account-settings-state'));
    if(update.currentVersion)root.append(this.node('p',this.text('当前版本：')+update.currentVersion+(update.targetVersion?' → '+update.targetVersion:''),'account-settings-version'));
    if(update.installation?.state==='verified')root.append(this.node('p',this.text('已核验电脑端安装版本已更新')+' · '+update.installation.previousBuild+' → '+update.installation.currentBuild,'account-settings-note'));
    if(update.message)root.append(this.node('p',update.message,'account-settings-note'));
    root.append(this.node('p','可远程查看新版本；下载和安装仍需在电脑端确认。','account-settings-note'));
    if(update.checkedAt)root.append(this.node('p',this.text('检查于：')+new Date(update.checkedAt*1000).toLocaleString(BridgeI18n.locale()),'account-settings-note'));
    const checking=['checking','requesting'].includes(update.state),denied=!this.desktop&&value.canSwitch===false,actions=this.node('div',undefined,'account-settings-actions');
    const check=this.button('查看 Codex 新版本',()=>this.perform({action:'checkDesktopUpdate'}));check.disabled=this.busy||checking||denied;actions.append(check);
    if(update.canRequest&&update.currentBuild){
      const request=this.button('打开电脑端更新窗口',()=>{
        if(window.confirm(this.text('确认所有桌面任务（包括未在网页显示的任务）已结束，并在电脑上打开 Codex 的“检查更新”界面？后续检查、下载与安装由 Codex 自行处理。')))
          return this.perform({action:'requestDesktopUpdate',requestId:this.requestId(),currentBuild:update.currentBuild,confirmed:true,tasksConfirmed:true});
      });request.disabled=this.busy||checking||denied;actions.append(request);
    }
    root.append(actions);
  }
}
