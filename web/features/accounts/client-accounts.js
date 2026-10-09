'use strict';
// Shared account snapshots for native clients; only the desktop host can enroll them.
class DesktopClientAccountsPanel {
  constructor({root,provider,read,request,readModels=null,desktop=false,visible=null,onChanged=()=>{},onUpdate=()=>{}}){
    Object.assign(this,{root,provider,read,request,readModels,desktop,onChanged,onUpdate});
    this.visible=visible||(()=>!document.hidden&&!root.closest('[hidden]')&&(!root.closest('dialog')||root.closest('dialog').open));
    this.value=null;this.generation=0;this.checkedAt=0;this.busy=false;this.loading=false;this.error='';this.models=null;this.modelsOpen=false;this.modelsRevision=0;
    this.languageListener=()=>{BridgeI18n.apply(this.root);this.render();};this.visibilityListener=()=>this.resume();
    document.addEventListener('bridge-language',this.languageListener);document.addEventListener('visibilitychange',this.visibilityListener);
    this.build();this.render();
  }
  text(value){return typeof BridgeI18n==='undefined'?value:BridgeI18n.t(value);}
  node(tag,text,cls){const node=document.createElement(tag);if(text!==undefined){node.textContent=this.text(text);if(typeof BridgeI18n!=='undefined'&&Object.hasOwn(BridgeI18n.dictionary,text))node.dataset.i18n=text;}if(cls)node.className=cls;return node;}
  button(text,fn){const button=this.node('button',text);button.type='button';button.onclick=fn;return button;}
  build(){
    this.root.classList.add('accounts-panel');this.root.replaceChildren();
    const toolbar=this.node('div',undefined,'accounts-toolbar');toolbar.append(this.node('h3','已保存的接入'));
    this.refreshButton=this.button('重新加载',()=>this.refresh());toolbar.append(this.refreshButton);this.root.append(toolbar);
    this.status=this.node('p',undefined,'account-muted');this.status.setAttribute('role','status');this.errorNode=this.node('p',undefined,'error');this.errorNode.setAttribute('role','alert');
    this.current=this.node('p',undefined,'accounts-current');this.modelsButton=this.button('查看可用模型',()=>this.toggleModels());this.modelsBox=this.node('div',undefined,'accounts-models');this.rows=this.node('div',undefined,'accounts-list');this.root.append(this.status,this.errorNode,this.current,this.modelsButton,this.modelsBox,this.rows);
    this.hint=this.node('p',undefined,'account-muted');this.root.append(this.hint);
    if(this.desktop){const name=this.node('label',undefined,'accounts-import-name');this.nameInput=this.node('input');this.nameInput.type='text';this.nameInput.maxLength=80;name.append(this.node('span','接入名称（可选）'),this.nameInput);this.root.append(name);this.importButton=this.button(this.provider==='deepseek'?'保存当前账号':'保存当前接入',()=>this.importCurrent());this.root.append(this.importButton);if(this.provider==='deepseek'){this.importApiButton=this.button('保存当前 API',()=>this.importCurrent('api'));this.root.append(this.importApiButton);}}
  }
  dispose(){this.generation++;clearTimeout(this.timer);this.disposed=true;document.removeEventListener('bridge-language',this.languageListener);document.removeEventListener('visibilitychange',this.visibilityListener);}
  resume(){clearTimeout(this.timer);if(this.disposed||!this.visible())return;if(!this.checkedAt||Date.now()-this.checkedAt>=300000)return this.refresh(false);this.schedule();}
  schedule(){clearTimeout(this.timer);if(!this.disposed&&this.visible())this.timer=setTimeout(()=>this.resume(),Math.max(0,300000-(Date.now()-this.checkedAt)));}
  activeIds(value=this.value){return new Set(value?.activeIds||[value?.activeId].filter(Boolean));}
  accept(value,notifyChange=true){
    if(!value||!Array.isArray(value.accounts))throw Error(this.text('账号信息暂不可用'));
    const previous=this.value,changed=this.value&&JSON.stringify([...this.activeIds()].sort())!==JSON.stringify([...this.activeIds(value)].sort());this.value=value;
    if(changed){this.modelsRevision++;this.modelsLoading=false;this.models=null;this.modelsOpen=false;if(notifyChange)this.onChanged(value,previous);}this.onUpdate(value);this.render();
  }
  async refresh(force=true){
    if(this.disposed||this.loading||this.busy)return;clearTimeout(this.timer);this.loading=true;this.error='';this.render();const generation=this.generation;
    try{
      const value=await this.read();if(generation!==this.generation)return;this.accept(value);
      if(this.visible())for(const row of this.value.accounts){
        if(!this.visible()||generation!==this.generation)break;
        const details=await this.request({action:'details',id:row.id,refresh:force});if(generation!==this.generation)return;this.accept(details);
      }
    }catch(error){if(generation===this.generation)this.error=error.message;}
    finally{if(generation===this.generation){this.checkedAt=Date.now();this.loading=false;this.render();if(this.value)this.onUpdate(this.value);this.schedule();}}
  }
  async perform(value){
    if(this.disposed||this.busy||this.loading)return false;clearTimeout(this.timer);this.busy=true;this.error='';this.render();const generation=this.generation;
    try{const result=await this.request(value);if(generation!==this.generation)return false;this.accept(result,value.action!=='import-current');return true;}
    catch(error){if(generation===this.generation)this.error=error.message;return false;}
    finally{if(generation===this.generation){this.busy=false;this.checkedAt=Date.now();this.render();if(this.value)this.onUpdate(this.value);this.schedule();}}
  }
  async importCurrent(kind){
    if(!this.desktop||this.busy||this.loading)return;
    if(this.provider==='claude'&&!window.confirm(this.text('保存当前账号会短暂重启应用；运行中的任务会阻止操作。')))return;
    const name=(this.nameInput?.value||'').trim();
    if(await this.perform({action:'import-current',...(kind?{kind}:{}),...(name?{name}:{})})){this.nameInput.value='';await this.refresh(false);}
  }
  choose(row){
    if(this.busy||this.loading||this.value?.canSwitch!==true||this.activeIds().has(row.id))return;
    if(window.confirm(this.text('切换会重启对应桌面应用。未发送草稿按账号保留，待发附件需重新添加。请先结束任务并保存工作，确认切换？')))return this.perform({action:'switch',id:row.id});
  }
  duration(minutes){return !minutes?'额度窗口':minutes%1440===0?minutes/1440+this.text(' 天'):minutes%60===0?minutes/60+this.text(' 小时'):minutes+this.text(' 分钟');}
  usage(box,row){
    const usage=row.usage||row.quota;
    if(row.kind==='api'&&!usage?.balance)return;
    let shown=false;
    for(const bucket of usage?.limits||[])for(const window of bucket.windows||[]){
      const line=this.node('div',undefined,'accounts-window'),percent=window.remainingPercent;
      const remaining=Number.isFinite(percent)?this.text('剩余 ')+Math.round(percent*10)/10+'%':window.remaining!=null?[window.remaining,window.total].filter(value=>value!=null).join(' / '):this.text('暂未提供');
      line.append(this.node('span',[bucket.name,this.text(this.duration(window.windowDurationMins))].filter(Boolean).join(' · ')),this.node('strong',remaining));
      if(Number.isFinite(percent)){const progress=this.node('progress');progress.max=100;progress.value=Math.min(100,Math.max(0,percent));progress.setAttribute('aria-label',this.text('剩余额度'));line.append(progress);}
      if(window.resetsAt)line.append(this.node('small',this.text('恢复时间：')+new Date(window.resetsAt*1000).toLocaleString(BridgeI18n.locale()),'account-reset-time'));
      box.append(line);shown=true;
    }
    for(const wallet of usage?.balance?.wallets||[]){
      const line=this.node('div',undefined,'accounts-window');line.append(this.node('span','账户余额'),this.node('strong',[wallet.currency,wallet.remaining].filter(value=>value!=null).join(' ')));
      if(wallet.toppedUp!=null||wallet.granted!=null)line.append(this.node('small',[wallet.toppedUp!=null?this.text('充值余额：')+wallet.toppedUp:'',wallet.granted!=null?this.text('赠送余额：')+wallet.granted:''].filter(Boolean).join(' · '),'account-reset-time'));
      box.append(line);shown=true;
    }
    if(!shown)box.append(this.node('span',usage?.status==='error'?(usage.error||'额度查询失败'):usage?.status==='loading'?'查询中…':usage?.status==='unsupported'?'暂未提供额度信息':!usage?'正在读取额度…':'暂未提供额度信息','account-muted'));
    if(shown&&usage?.status==='error')box.append(this.node('small','保留上次结果，数据尚未更新','account-muted'));
    if(usage?.checkedAt)box.append(this.node('small',this.text('更新于：')+new Date(usage.checkedAt*1000).toLocaleString(BridgeI18n.locale()),'account-updated'));
    const refresh=this.button('查看剩余额度',()=>this.perform({action:'details',id:row.id,refresh:true}));refresh.disabled=this.busy||this.loading;box.append(refresh);
  }
  async toggleModels(){
    this.modelsOpen=!this.modelsOpen;if(!this.modelsOpen||this.models){this.render();return;}
    return this.loadModels();
  }
  async loadModels(){
    const generation=this.generation,revision=++this.modelsRevision;this.modelsError='';this.modelsLoading=true;this.render();
    try{const value=await this.readModels();if(generation!==this.generation||revision!==this.modelsRevision)return;this.models=value;}
    catch(error){if(generation===this.generation&&revision===this.modelsRevision)this.modelsError=error.message;}
    finally{if(generation===this.generation&&revision===this.modelsRevision){this.modelsLoading=false;this.render();}}
  }
  renderModels(box){
    if(this.modelsLoading){box.append(this.node('p','正在读取模型…','account-muted'));return;}
    if(this.modelsError){box.append(this.node('p',this.modelsError,'error'),this.button('重试',()=>this.loadModels()));return;}
    let count=0;const groups=this.models?.groups?.length?this.models.groups:[{models:this.models?.models||this.models?.catalog?.models||[]}];
    for(const group of groups){
      if(!group.models?.length)continue;count+=group.models.length;
      box.append(this.node('strong',[group.surface==='code'?'Code':group.surface==='cowork'?'Cowork':group.label||group.name||group.id,this.text(group.source==='session-history'?'历史模型':'可用模型')].filter(Boolean).join(' · ')));
      if(group.notice)box.append(this.node('p',group.notice,'account-muted'));
      const list=this.node('ul');for(const model of group.models){const row=this.node('li',typeof model==='string'?model:model.name||model.id);if(model.efforts?.length)row.append(this.node('small',' · '+model.efforts.map(effort=>this.text(typeof effort==='string'?effort:effort.name||effort.id)).join(' / '),'account-muted'));list.append(row);}box.append(list);
    }
    if(!count)box.append(this.node('p','当前客户端未返回可用模型，请检查客户端登录与模型配置。','account-muted'));
  }
  render(){
    const value=this.value;this.refreshButton.disabled=this.busy||this.loading;
    this.status.textContent=this.text(this.busy?'正在处理…':this.loading?'正在读取…':value?.message||(value?.gatewayRunning===false?'网关未启动':''));this.errorNode.textContent=this.text(this.error||'');
    this.current.textContent=this.text('当前接入：')+(value?.current?.name||value?.current?.label||value?.accounts.find(row=>row.id===value.activeId)?.name||this.text('暂未识别'))+(value?.current?.routes?.length?' · '+this.text('已配置接入：')+value.current.routes.map(route=>this.text(({'deepseek-account':'官方账号','deepseek-official':'API 接入'})[route]||route)).join(' / '):'');
    this.hint.textContent=this.text(this.desktop?'在原桌面应用登录账号或配置 API 后，保存当前接入以便切换。':'添加账号或 API，请在电脑端完成。')+(this.provider==='deepseek'?' '+this.text('现有会话继续使用各自选择的模型接入。'):'');
    this.modelsButton.hidden=!this.readModels;this.modelsButton.textContent=this.text(this.modelsOpen?'收起模型':'查看可用模型');this.modelsButton.disabled=this.busy||this.modelsLoading;this.modelsBox.hidden=!this.modelsOpen;this.modelsBox.replaceChildren();if(this.modelsOpen)this.renderModels(this.modelsBox);
    if(this.nameInput)this.nameInput.disabled=this.busy||this.loading;
    for(const button of [this.importButton,this.importApiButton])if(button)button.disabled=this.busy||this.loading||!value||value.canImport===false;
    this.rows.replaceChildren();
    const activeIds=this.activeIds(),accounts=[...(value?.accounts||[])].sort((a,b)=>Number(activeIds.has(b.id))-Number(activeIds.has(a.id)));
    if(value&&!accounts.length)this.rows.append(this.node('p','尚未保存此客户端的接入。','account-muted'));
    for(const row of accounts){
      const active=activeIds.has(row.id),card=this.node('section',undefined,'account-bucket'+(active?' is-current':'')),identity=this.node('div',undefined,'accounts-identity'),name=this.node('div',undefined,'accounts-name'),heading=this.node('div',undefined,'accounts-name-heading');
      heading.append(this.node('h3',row.name||row.email||row.id));if(row.saved===false)heading.append(this.node('span','尚未保存','account-muted'));if(active)heading.append(this.node('span','当前接入','accounts-plan'));name.append(heading);
      name.append(this.node('p',row.kind==='api'?'API 接入':'官方账号','account-muted'));if(row.email)name.append(this.node('p',row.email,'account-muted'));identity.append(name);
      const usage=this.node('div',undefined,'accounts-usage');this.usage(usage,row);if(usage.children.length)identity.append(usage);card.append(identity);
      const actions=this.node('div',undefined,'accounts-actions'),switcher=this.button(active?'当前接入':'切换到此接入',()=>this.choose(row));switcher.disabled=active||this.busy||this.loading||value.canSwitch!==true;actions.append(switcher);

      card.append(actions);this.rows.append(card);
    }
  }
}
if(typeof module!=='undefined')module.exports={DesktopClientAccountsPanel};
