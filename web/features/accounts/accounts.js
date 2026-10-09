/* Shared account selector; enrollment controls exist only in the desktop host. */
class AccountsPanel {
  constructor({root,read,request,desktop=false,toolbar=null,onChanged=()=>{},onUpdate=()=>{},onReset=null}){
    Object.assign(this,{root,read,request,desktop,toolbar,onChanged,onUpdate,onReset});this.value=null;this.busy=false;this.timer=null;this.generation=0;this.operationError=false;this.expandedModels=new Set();
    this.build();
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
    this.root.append(this.node('p','切换将重启此电脑的 Codex 桌面应用，手机网关保持运行。','account-muted'));
    this.status=this.node('p',undefined,'account-muted');this.status.setAttribute('role','status');
    this.error=this.node('p',undefined,'error');this.error.setAttribute('role','alert');
    this.rows=this.node('div',undefined,'accounts-list');
    this.currentReset=this.button('查看并使用重置卡',()=>this.onReset?.());this.currentReset.hidden=true;
    this.current=this.node('p',undefined,'accounts-current');this.blocked=this.node('div',undefined,'account-muted');this.root.append(this.current,this.status,this.error,this.currentReset,this.blocked,this.rows);
    this.management=this.node('div',undefined,'accounts-management');this.root.append(this.management);
    this.tools=this.node('details');this.tools.append(this.node('summary','提醒与 Codex Desktop 更新'));
    this.reminders=this.node('div');this.updates=this.node('section');this.tools.append(this.reminders,this.updates);this.management.append(this.tools);
    if(!this.desktop){this.root.append(this.node('p','添加、修改与删除账号请在电脑端完成。','account-muted'));return;}
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
    this.modelsButton=this.button('获取上游模型',async()=>{
      const context=()=>JSON.stringify([this.importId,this.editId,this.kind.value,this.url.input.value,this.key.input.value]),started=context();
      const payload=this.importId?{action:'models',candidateId:this.importId}:{action:'models',id:this.editId,baseUrl:this.url.input.value,apiKey:this.key.input.value};
      if(await this.perform(payload)){if(started!==context())return;this.modelOptions.replaceChildren(this.node('option','请选择模型'));
        this.modelOptions.children[0].value='';for(const id of this.value.models||[]){const option=this.node('option');option.textContent=id;option.value=id;this.modelOptions.append(option);}
        this.modelOptions.hidden=false;this.modelMessage.textContent=this.text('模型列表不保证支持 Responses API，请选择可用于对话的模型。');}
    });
    this.modelMessage=this.node('p',undefined,'account-muted');
    const clearModels=()=>{this.modelOptions.replaceChildren();this.modelOptions.hidden=true;this.modelMessage.textContent='';};
    this.url.input.oninput=clearModels;this.key.input.oninput=clearModels;this.clearModels=clearModels;
    const fields=()=>{this.modelsButton.hidden=this.kind.value!=='api';if(this.kind.value!=='api')clearModels();for(const field of [this.url,this.key,this.model])field.wrap.hidden=this.kind.value!=='api';};this.kind.onchange=fields;fields();
    this.submit=this.node('button','继续');this.submit.type='submit';
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
    settings.append(this.executable.wrap,this.scanDesktop,this.button('保存程序路径',()=>this.perform({action:'configure',desktopExecutable:this.executable.input.value})));
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
    if(this.busy)return false;clearTimeout(this.timer);this.generation++;this.loading=false;this.busy=true;this.operationError=false;this.error.textContent='';this.render();const generation=this.generation;
    try{const value=await this.request(payload);if(generation===this.generation){this.accept(value);return true;}return false;}
    catch(error){if(generation===this.generation){this.operationError=true;this.error.textContent=this.text(String(error.message).replace(/^Error invoking remote method '[^']+': (?:Error: )?/,''));}return false;}
    finally{if(generation===this.generation){this.busy=false;this.render();this.timer=setTimeout(()=>this.refresh(),2000);}}
  }
  async refresh(){
    clearTimeout(this.timer);if(this.loading||this.busy)return;this.loading=true;const generation=this.generation;
    try{const value=await this.read();if(generation===this.generation){if(!this.operationError)this.error.textContent='';this.accept(value);await this.loadUsage(generation);}}
    catch(error){if(generation===this.generation)this.error.textContent=this.text(String(error.message).replace(/^Error invoking remote method '[^']+': (?:Error: )?/,''));}
    finally{if(generation===this.generation){this.loading=false;this.render();this.timer=setTimeout(()=>{const dialog=this.root.closest('dialog');if(!this.root.closest('[hidden]')&&!document.hidden&&(!dialog||dialog.open))this.refresh();},2000);}}
  }
  visible(){const dialog=this.root.closest('dialog');return !this.root.closest('[hidden]')&&!document.hidden&&(!dialog||dialog.open);}
  async loadUsage(generation){
    if(!this.visible())return;
    if(!['idle','complete','restored','failed'].includes(this.value?.switch?.phase||'idle'))return;
    for(const row of this.value?.accounts||[]){
      const usage=row.details?.usage;
      if(row.kind!=='chatgpt'||usage?.status==='loading'||(usage&&Date.now()/1000-(usage.checkedAt||0)<300))continue;
      if(generation!==this.generation||this.busy)return;
      const value=await this.request({action:'details',id:row.id,section:'usage'});
      if(generation===this.generation&&!this.busy)this.accept(value);
    }
  }
  relative(minutes){const days=Math.floor(minutes/1440),hours=Math.floor(minutes%1440/60),rest=minutes%60;return [days?days+this.text(' 天'):'',hours?hours+this.text(' 小时'):'',rest?rest+this.text(' 分钟'):''].filter(Boolean).join(' ');}
  resetTime(stamp){const minutes=Math.ceil((stamp*1000-Date.now())/60000);return (minutes>0?this.relative(minutes)+this.text('后恢复'):this.text('等待刷新'))+' · '+new Date(stamp*1000).toLocaleString(BridgeI18n.locale());}
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
        for(const bucket of usage.limits||[])if(bucket.credits){const credit=bucket.credits;box.append(this.node('p',bucket.name+' · Credits: '+(credit.unlimited?this.text('不限量'):credit.balance??this.text('暂未提供')),'account-muted'));}
        const period=usage.subscription?.periodEndsAt,online=usage.subscription?.source==='online';
        if(Number.isFinite(period)&&(online||period>Date.now()/1000))box.append(this.node('p',this.text(online?'查询到的订阅周期截止日期：':'登录记录中的订阅日期（非实时）：')+new Date(period*1000).toLocaleString(BridgeI18n.locale()),'account-subscription'));
        else box.append(this.node('p',this.text('订阅有效期暂未确认'),'account-subscription'));
        if(usage.subscription?.error)box.append(this.node('p',this.text(usage.subscription.error),'account-muted'));
        box.append(this.node('p',this.text(online?'订阅日期来自在线查询，续订与扣费状态请以 ChatGPT 订阅页面为准。':'登录记录可能滞后，请以 ChatGPT 订阅页面为准。'),'account-muted'));
        if(usage.updatedAt)box.append(this.node('small',this.text('更新于：')+new Date(usage.updatedAt*1000).toLocaleString(BridgeI18n.locale())+(usage.status==='loading'?' · '+this.text('查询中…'):''),'account-updated'));
        if(usage.status==='error')box.append(this.node('small','保留上次结果，数据尚未更新','account-muted'));
        const expiry=usage.resetCredits?.credits?.filter(c=>c.status==='available'&&c.resetType==='codexRateLimits'&&typeof c.expiresAt==='number'&&c.expiresAt>Date.now()/1000).map(c=>c.expiresAt).sort((a,b)=>a-b)[0];
        if(expiry)box.append(this.node('small',this.text('最近一张重置卡到期：')+new Date(expiry*1000).toLocaleString(BridgeI18n.locale()),'account-muted'));
      }
      const refresh=this.button('查看剩余额度',()=>this.perform({action:'details',id:row.id,section:'usage',refresh:true}));refresh.disabled=this.busy||usage?.status==='loading';box.append(refresh);card.append(box);
    }
  }
  async openReset(row){
    if(this.resetDialog?.open)return;
    const dialog=this.node('dialog',undefined,'picker account-reset-dialog'),heading=this.node('h2',this.text('使用重置卡')+' · '+row.name);
    const close=this.button('关闭',()=>dialog.close()),content=this.node('div'),button=this.node('button');
    const header=this.node('div',undefined,'picker-head');header.append(heading,close);dialog.append(header,content);
    const panel=new AccountPanel({root:content,button,
      read:refresh=>this.request({action:'account',id:row.id,operation:'read',refresh}),
      consume:value=>this.request({action:'account',id:row.id,operation:'consume',...value}),
      onConsumed:()=>this.perform({action:'details',id:row.id,section:'usage',refresh:true}),
      visible:()=>dialog.open&&this.visible()});
    this.resetDialog=dialog;this.resetPanel=panel;this.resetAccountId=row.id;
    dialog.onclose=()=>{panel.clear();dialog.remove();this.resetDialog=null;this.resetPanel=null;this.resetAccountId=null;};
    document.body.append(dialog);dialog.showModal();await panel.refresh();
  }
  accountModels(card,row){
    const models=row.details?.models,opened=this.expandedModels.has(row.id);
    const show=this.button(opened?'收起模型':'查看可用模型',()=>{if(opened){this.expandedModels.delete(row.id);this.render();}else{this.expandedModels.add(row.id);this.perform({action:'details',id:row.id,section:'models'});}});show.disabled=this.busy;card.append(show);
    if(opened){const box=this.node('div',undefined,'accounts-models');
      if(!models||models.status==='loading')box.append(this.node('p','正在读取模型…'));
      else if(models.status==='error'){box.append(this.node('p',models.error));box.append(this.button('重试',()=>this.perform({action:'details',id:row.id,section:'models',refresh:true})));}
      else if(!models.models?.length)box.append(this.node('p','暂未提供可用模型'));
      else for(const model of models.models){const line=this.node('div');line.textContent=model.name===model.id?model.id:model.name+' · '+model.id;box.append(line);}card.append(box);
    }
  }
  accept(value){const old=this.value;if(!this.desktop&&old?.canSwitch===false&&value.canSwitch===undefined)value={...value,canSwitch:false};this.value=value;if(old&&(old.activeId!==value.activeId||old.current?.kind!==value.current?.kind||old.current?.name!==value.current?.name||old.switch?.phase!==value.switch?.phase))this.onChanged(value);this.onUpdate(value);this.render();}
  clear(){this.resetDialog?.close();clearTimeout(this.timer);this.generation++;this.value=null;this.loading=false;this.busy=false;this.operationError=false;this.error.textContent='';this.rows.replaceChildren();}
  requestId(){
    if(typeof crypto.randomUUID==='function')return crypto.randomUUID();
    const bytes=crypto.getRandomValues(new Uint8Array(16));bytes[6]=(bytes[6]&15)|64;bytes[8]=(bytes[8]&63)|128;
    const h=[...bytes].map(x=>x.toString(16).padStart(2,'0')).join('');return h.slice(0,8)+'-'+h.slice(8,12)+'-'+h.slice(12,16)+'-'+h.slice(16,20)+'-'+h.slice(20);
  }
  async choose(row){
    if(!window.confirm(this.text('确认所有桌面任务（包括未在网页显示的任务）已结束，并切换接入、重启 Codex 桌面应用？')+'\n'+row.name))return;
    try{await this.perform({action:'switch',id:row.id,requestId:this.requestId(),confirmed:true,tasksConfirmed:true});this.refresh();}
    catch(error){this.operationError=true;this.error.textContent=this.text('无法创建切换请求，请刷新页面后重试');}
  }
  renderTools(){
    const value=this.value;if(!value)return;
    this.reminders.replaceChildren();
    this.reminders.append(this.node('p','提醒通过电脑端已配置的 Bark、ntfy 或 PushPlus 发送，电脑与网关需要保持运行。','account-muted'));
    const labels={lowQuota:'额度不足 10% 时提醒',quotaReset:'确认额度恢复后提醒',resetExpiry:'重置卡到期前 24 小时提醒',subscriptionExpiry:'订阅周期截止前 7／3／1 天提醒',desktopUpdate:'Codex Desktop 新版本提醒'};
    for(const [key,label] of Object.entries(labels)){
      const wrap=this.node('label',undefined,'account-reminder'),input=document.createElement('input');input.type='checkbox';input.checked=!!value.reminders?.[key];input.disabled=this.busy;
      input.onchange=()=>this.perform({action:'reminders',preferences:{[key]:input.checked}});wrap.append(input,this.node('span',label));this.reminders.append(wrap);
    }
    if(value.reminderError)this.reminders.append(this.node('p',value.reminderError,'error'));
    const update=value.desktopUpdate||{state:'idle'},labelsUpdate={idle:'尚未检查',checking:'正在检查官方更新渠道…',available:'官方渠道有新版本',checked:'本次未发现更高的公开版本',unsupported:'需要在电脑端检查',error:'更新检查失败',requesting:'正在打开原生更新器…',needsDesktop:'等待电脑端确认'};
    this.updates.replaceChildren(this.node('h3','Codex Desktop 更新'),this.node('p',update.failureReason?'未能打开更新器':labelsUpdate[update.state]||update.state));
    this.updates.append(this.node('p','可远程查看新版本；下载和安装仍需在电脑端确认。','account-muted'));
    if(update.currentVersion)this.updates.append(this.node('p',this.text('当前版本：')+update.currentVersion+(update.targetVersion?' → '+update.targetVersion:''),'account-muted'));
    if(update.message)this.updates.append(this.node('p',update.message,'account-muted'));
    if(update.checkedAt)this.updates.append(this.node('small',this.text('检查于：')+new Date(update.checkedAt*1000).toLocaleString(BridgeI18n.locale()),'account-muted'));
    const checking=['checking','requesting'].includes(update.state),denied=!this.desktop&&value.canSwitch===false;
    const check=this.button('查看 Codex 新版本',()=>this.perform({action:'checkDesktopUpdate'}));check.disabled=this.busy||checking||denied;this.updates.append(check);
    if(update.canRequest&&update.currentBuild){const request=this.button('打开电脑端更新窗口',()=>{
      if(window.confirm(this.text('确认所有桌面任务（包括未在网页显示的任务）已结束，并在电脑上打开 Codex 的“检查更新”界面？后续检查、下载与安装由 Codex 自行处理。')))
        this.perform({action:'requestDesktopUpdate',requestId:this.requestId(),currentBuild:update.currentBuild,confirmed:true,tasksConfirmed:true});
    });request.disabled=this.busy||checking||denied;this.updates.append(request);}
  }
  render(){
    const value=this.value;if(!value)return;
    const phase=value.switch?.phase||'idle',labels={idle:'请选择已保存的接入',preparing:'正在准备账号',stopping:'正在退出 Codex 桌面应用',applying:'正在应用接入配置',starting:'正在启动 Codex 桌面应用',verifying:'正在核验账号与桌面连接',complete:'切换完成',restoring:'正在恢复原接入',restored:'已恢复原接入',failed:'切换未完成',interrupted:'需要在桌面端恢复原接入'};
    const switching=!['idle','complete','restored','failed'].includes(phase);
    if(this.resetDialog?.open&&(switching||!value.accounts.some(row=>row.id===this.resetAccountId)))this.resetDialog.close();
    this.status.textContent=this.text(labels[phase]||phase)+(value.switch?.error?' · '+this.text(value.switch.error):'');
    this.status.hidden=phase==='idle'&&value.current?.status==='ready';
    const current=value.current;this.current.textContent=this.text('当前接入：')+(current?.status==='ready'?(current.kind==='signedOut'?this.text('Codex 未登录'):current.name+(current.id?'':this.text('（未保存到列表）'))):this.text(current?.status==='checking'?'正在识别…':'暂未识别'));
    this.currentReset.hidden=!this.onReset||current?.kind!=='chatgpt'||!!value.activeId;this.currentReset.disabled=this.busy||switching;
    this.blocked.replaceChildren();for(const row of value.blockers||[]){const line=this.node('p');line.textContent=this.text(({approval:'待确认',running:'运行中',queued:'排队中',unknown:'发送结果未确认'})[row.reason]||'运行中')+' · '+row.title;if(row.reason==='unknown'&&row.submissionId){
      const ignore=this.button('忽略',()=>{if(window.confirm(this.text('忽略只会移除未确认提示，不会撤回或重发消息。请先检查聊天记录。')))return this.perform({action:'ignoreSubmission',threadId:row.id,submissionId:row.submissionId,host:row.host||'local'});});
      ignore.disabled=this.busy||switching;line.append(ignore);
    }this.blocked.append(line);}
    this.refreshButton.disabled=this.loading;this.rows.replaceChildren();this.renderTools();
    if(!value.accounts.length)this.rows.append(this.node('p','尚未添加账号。请在电脑端添加官方账号或自定义 API。'));
    for(const row of [...value.accounts].sort((a,b)=>Number(b.id===value.activeId)-Number(a.id===value.activeId))){
      const card=this.node('section',undefined,'account-bucket'),active=value.activeId===row.id;
      if(active)card.classList.add('is-current');
      const actions=this.node('div',undefined,'accounts-actions');
      const identity=this.node('div',undefined,'accounts-identity'),name=this.node('div',undefined,'accounts-name');
      const heading=this.node('h3');heading.textContent=row.name;const title=this.node('div',undefined,'accounts-name-heading');title.append(heading);
      if(row.kind==='chatgpt'){
        if(row.details?.usage?.planType||row.planType)title.append(this.node('span',row.details?.usage?.planType||row.planType,'accounts-plan'));
        const usage=row.details?.usage;
        const count=usage?.resetCredits?.availableCount;
        if(this.onReset){
          const reset=this.button('使用重置卡',()=>this.openReset(row));reset.classList.add('accounts-reset');reset.disabled=this.busy||switching;
          reset.setAttribute('aria-label',this.text('查看并使用所选账号的重置卡'));title.append(reset);
        }
        if(usage?.resetCredits)title.append(this.node('span',this.text('重置卡')+' · '+(count??this.text('暂未提供')),'accounts-credit'));
      }
      name.append(title,this.node('p',row.kind==='chatgpt'?(row.email||this.text('官方 ChatGPT 账号')):(row.baseUrl+' · '+row.model),'account-muted'));
      identity.append(name);this.accountUsage(identity,row);card.append(identity);
      const choose=this.button(active?'当前接入':'切换到此接入',()=>this.choose(row));choose.disabled=this.busy||switching||active||(!this.desktop&&value.canSwitch===false);actions.append(choose);
      if(this.desktop){
        const edit=this.button(row.kind==='chatgpt'?'重新登录':'修改',()=>{this.resetForm();this.editId=row.id;this.addDetails.open=true;this.name.input.value=row.name;this.kind.value=row.kind;this.kind.disabled=true;this.kind.onchange();this.url.input.value=row.baseUrl||'';this.model.input.value=row.model||'';this.key.input.value='';this.name.input.focus();});
        edit.hidden=active;edit.disabled=this.busy||switching||active;
        const rename=this.button('重命名',()=>{this.renameId=row.id;this.renameName.input.value=row.name;this.renameForm.hidden=false;this.renameName.input.focus();});rename.disabled=this.busy||switching;
        const remove=this.button('删除',()=>{if(window.confirm(this.text('删除此账号档案？')+'\n'+row.name))this.perform({action:'delete',id:row.id});});remove.hidden=active;remove.disabled=this.busy||switching||active;
        actions.append(edit,rename,remove);
      }
      this.accountModels(actions,row);card.append(actions);this.rows.append(card);
    }
    if(!this.desktop&&value.canSwitch===false)this.rows.append(this.node('p','免密访问不能切换账号，请在桌面端操作'));
    if(this.desktop){
      this.scanDesktop.disabled=this.busy||switching;this.renameSave.disabled=this.busy||switching;this.scanButton.disabled=this.busy||switching;this.modelsButton.disabled=this.busy||switching;
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
      this.recover.disabled=this.busy||phase!=='interrupted';this.submit.disabled=this.busy||switching||['starting','waiting'].includes(value.enrollment?.phase);
      this.loginBox.replaceChildren();const login=value.enrollment;
      if(login){
        this.loginBox.append(this.node('p',({starting:'正在准备登录',waiting:'请在此电脑的浏览器完成官方登录',complete:'账号已添加',failed:'登录未完成，请重新添加'})[login.phase]||''));
        if(login.phase==='waiting'&&login.authUrl){this.loginBox.append(this.button('打开官方登录',()=>this.perform({action:'openLogin'})));}
        if(['starting','waiting'].includes(login.phase))this.loginBox.append(this.button('取消登录',()=>this.perform({action:'cancelLogin'})));
      }
    }
  }
}
