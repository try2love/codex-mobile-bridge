'use strict';

class WorkModes {
  constructor({select,hint,goalRoot,goalToggle,storage=sessionStorage,onCancel,onStatus,onEdit,newId=()=>crypto.randomUUID()}) {
    this.select=select;this.hint=hint;this.goalRoot=goalRoot;this.goalToggle=goalToggle;this.storage=storage;this.onCancel=onCancel;this.onStatus=onStatus;this.onEdit=onEdit;this.newId=newId;this.sending=false;this.editing=false;this.editText="";
    this.key=null;this.selection=null;this.view=null;this.cancelBusy=false;this.statusBusy=null;this.actionError='';
    select.onchange=()=>{this.selection=select.value;this.storage.setItem('work-mode:'+this.key,this.selection);this.actionError='';this.render();};
    goalToggle.onclick=()=>{this.storage.removeItem('hidden-goal:'+this.key);this.render();this.goalRoot.querySelector('.goal-close')?.focus();};
  }
  open(key) {
    this.key=key;const saved=this.storage.getItem('work-mode:'+key);
    this.selection=['default','plan','goal'].includes(saved)?saved:null;
    this.view=null;this.cancelBusy=false;this.statusBusy=null;this.sending=false;this.editing=false;this.editText='';this.actionError='';this.render();
  }
  value(sendMode) {return sendMode==='steer'?null:this.select.value;}
  submitted(key,mode) {
    if(mode!=='goal')return;
    this.storage.setItem('work-mode:'+key,'default');
    if(key===this.key){this.selection='default';this.cancelBusy=false;this.statusBusy=null;this.actionError='';this.render();}
  }
  async action(kind,fields,run) {
    if(this.busy())return;
    const key=this.key,storageKey='goal-action:'+key;
    const goal=this.view?.goal;
    const expected=goal?Object.fromEntries(['objective','status','createdAt','createdAtMs','goalId'].filter(k=>goal[k]!=null).map(k=>[k,goal[k]])):{};
    const fingerprint=JSON.stringify({kind,fields,expected});
    let pending;try{pending=JSON.parse(this.storage.getItem(storageKey));}catch{}
    if(!pending||pending.fingerprint!==fingerprint)pending={fingerprint,payload:{id:this.newId(),...fields,expected,uiLocale:BridgeI18n.locale()}};
    this.storage.setItem(storageKey,JSON.stringify(pending));
    this.cancelBusy=kind==='cancel';this.statusBusy=kind==='cancel'?null:kind;this.actionError='';this.render();
    try{await run(pending.payload);this.storage.removeItem(storageKey);if(this.key===key)this.editing=false;}
    catch(error){if(this.key===key)this.actionError=BridgeI18n.t(error.message||'目标操作失败，请重试');}
    finally{if(this.key===key){this.cancelBusy=false;this.statusBusy=null;this.render();}}
  }
  cancel(){return this.action('cancel',{},payload=>this.onCancel(payload));}
  setStatus(status){return this.action(status,{status},payload=>this.onStatus(payload));}
  saveEdit(){return this.action('edit',{objective:this.editText.trim()},payload=>this.onEdit(payload));}
  busy(){return !!(this.sending||this.cancelBusy||this.statusBusy);}
  render(view=this.view,sendMode=this.sendMode,sending=this.sending) {
    this.view=view;this.sendMode=sendMode;this.sending=!!sending;
    const busy=this.busy();
    const t=BridgeI18n.t,goalSupported=!!view&&view.host==='local'&&view.goalRuntimeAvailable!==false;
    const goalUnavailableReason=!view||view.host!=='local'?'目标模式暂不支持 SSH 主机':'未找到桌面 App 的 Codex 运行时';
    this.select.value=this.selection||(view?.collaborationMode==='plan'?'plan':'default');
    if(!goalSupported&&this.select.value==='goal')this.select.value='default';
    this.select.disabled=!view||view.loadingHistory||view.activating||busy||sendMode==='steer';
    const goalOption=this.select.querySelector('[value=goal]');
    goalOption.disabled=!goalSupported||!!(view?.status==='active'||(view?.goal&&view.goal.status!=='complete')||['pending','unknown'].includes(view?.goalSubmission?.status));
    goalOption.title=t(goalSupported?'':goalUnavailableReason);
    this.hint.textContent=t(sendMode==='steer'?'补充内容沿用当前任务模式':this.select.value==='plan'?'先讨论并制定计划，确认后执行。':this.select.value==='goal'?(goalSupported?'设定目标后持续执行，直到完成、暂停或取消。':goalUnavailableReason):'');
    this.hint.hidden=!this.hint.textContent;
    this.select.title=this.hint.textContent;
    const goal=view?.goal,request=view?.goalSubmission;
    const goalIdentity=request?JSON.stringify(['request',request.id]):goal?JSON.stringify(['goal',goal.createdAt,goal.objective]):null;
    const dismissed=!!goalIdentity&&this.storage.getItem('hidden-goal:'+this.key)===goalIdentity;
    this.goalToggle.hidden=!(goal||request);
    const expanded=this.goalRoot.firstElementChild?.open||false;
    this.goalRoot.replaceChildren();this.goalRoot.hidden=!goalIdentity||dismissed;
    const node=(tag,text,cls)=>{const el=document.createElement(tag);el.textContent=text;if(cls)el.className=cls;return el;};
    const button=(text,cls,onclick,disabled)=>{const el=node('button',text,cls);el.type='button';el.disabled=!!disabled;el.onclick=onclick;return el;};
    if(goal){
      const labels={active:'目标进行中',paused:'目标已暂停',blocked:'目标等待处理',usageLimited:'目标已达到用量限制',budgetLimited:'目标已达到预算限制',complete:'目标已完成'};
      const details=document.createElement('details');details.open=expanded;
      details.append(node('summary',t(labels[goal.status]||'目标状态')));
      details.append(node('p',goal.objective));
      if(Number.isFinite(goal.tokensUsed))details.append(node('p',t('已用 Token：')+goal.tokensUsed.toLocaleString()+(Number.isFinite(goal.tokenBudget)?' / '+goal.tokenBudget.toLocaleString():''),'muted'));
      this.goalRoot.append(details);
    }
    if(request){
      const labels={pending:'正在开启原生目标…',unknown:'目标请求送达结果尚未确认，请查看会话。',unconfirmed:'尚未确认目标已开启，请刷新或查看 Codex。'};
      this.goalRoot.append(node('p',t(labels[request.status]||labels.unknown),'goal-pending'));
    }
    if(this.actionError)this.goalRoot.append(node('p',this.actionError,'error'));
    if(goal&&goalSupported){
      const actions=node('div','','goal-actions');
      this.goalRoot.append(actions);
      if(['active','blocked','usageLimited','budgetLimited'].includes(goal.status)){
        actions.append(button(this.statusBusy==='paused'?t('正在暂停目标…'):t('暂停目标'),'goal-status secondary',()=>this.setStatus('paused'),this.busy()));
      }
      if(goal.status==='paused'){
        actions.append(button(this.statusBusy==='active'?t('正在恢复目标…'):t('恢复目标'),'goal-status primary',()=>this.setStatus('active'),this.busy()));
      }
      if(goal.status==='paused')actions.append(button(t('修改目标'),'goal-edit secondary',()=>{this.editing=true;this.editText=goal.objective;this.render();this.goalRoot.querySelector('.goal-editor')?.focus();},this.busy()));
      actions.append(button(this.cancelBusy?t('正在关闭目标…'):t('关闭目标'),'goal-cancel secondary',()=>this.cancel(),this.busy()));
      this.goalRoot.append(node('p',t('暂停或关闭目标不会停止当前回复；如需立即停止，请点击“停止”。'),'muted'));
      if(goal.status==='active')this.goalRoot.append(node('p',t('修改目标前请先暂停。'),'muted'));
      if(this.editing&&goal.status==='paused'){
        const input=node('textarea','', 'goal-editor');input.value=this.editText;input.maxLength=4000;input.rows=3;input.disabled=this.busy();input.setAttribute('aria-label',t('目标内容'));input.oninput=()=>{this.editText=input.value;};
        this.goalRoot.append(input,node('p',t('修改目标内容会重置用量统计，保存后保持暂停。'),'muted'));
        this.goalRoot.append(button(t('保存目标'),'goal-save primary',()=>this.saveEdit(),this.busy()),button(t('放弃修改'),'secondary',()=>{this.editing=false;this.render();},this.busy()));
      }
    }
    if(goalIdentity){
      const close=node('button','×','goal-close');close.type='button';close.title=t('隐藏目标栏');close.setAttribute('aria-label',close.title);
      close.onclick=()=>{this.storage.setItem('hidden-goal:'+this.key,goalIdentity);this.render();this.goalToggle.focus();};
      this.goalRoot.append(close);
    }
  }
}

if(typeof module!=='undefined')module.exports={WorkModes};
