'use strict';

class WorkModes {
  constructor({select,hint,goalRoot,goalToggle,storage=sessionStorage,onCancel,onStatus}) {
    this.select=select;this.hint=hint;this.goalRoot=goalRoot;this.goalToggle=goalToggle;this.storage=storage;this.onCancel=onCancel;this.onStatus=onStatus;
    this.key=null;this.selection=null;this.view=null;this.cancelBusy=false;this.statusBusy=null;this.actionError='';
    select.onchange=()=>{this.selection=select.value;this.storage.setItem('work-mode:'+this.key,this.selection);this.actionError='';this.render();};
    goalToggle.onclick=()=>{this.storage.removeItem('hidden-goal:'+this.key);this.render();this.goalRoot.querySelector('.goal-close')?.focus();};
  }
  open(key) {
    this.key=key;const saved=this.storage.getItem('work-mode:'+key);
    this.selection=['default','plan','goal'].includes(saved)?saved:null;
    this.view=null;this.cancelBusy=false;this.statusBusy=null;this.actionError='';this.render();
  }
  value(sendMode) {return sendMode==='steer'?null:this.select.value;}
  submitted(key,mode) {
    if(mode!=='goal')return;
    this.storage.setItem('work-mode:'+key,'default');
    if(key===this.key){this.selection='default';this.cancelBusy=false;this.statusBusy=null;this.actionError='';this.render();}
  }
  async action(kind,run) {
    if(this.busy())return;
    this.cancelBusy=kind==='cancel'?true:null;this.statusBusy=kind==='cancel'?null:kind;this.actionError='';this.render();
    try{await run();}
    catch(error){this.actionError=BridgeI18n.t(error.message||'目标操作失败，请重试');}
    finally{this.cancelBusy=false;this.statusBusy=null;this.render();}
  }
  cancel(){return this.action('cancel',()=>this.onCancel());}
  setStatus(status){return this.action(status,()=>this.onStatus(status));}
  busy(){return this.cancelBusy||this.statusBusy!=null;}
  render(view=this.view,sendMode=this.sendMode,busy=this.busy) {
    this.view=view;this.sendMode=sendMode;this.busyArgument=busy;this.busy=()=>!!busy;
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
    if(goal&&goalSupported&&goal.status!=='complete'){
      if(goal.status==='active'){
        this.goalRoot.append(button(this.statusBusy==='paused'?t('正在暂停目标…'):t('暂停目标'),'goal-status secondary',()=>this.setStatus('paused'),this.busy()));
      }
      if(goal.status==='paused'){
        this.goalRoot.append(button(this.statusBusy==='active'?t('正在恢复目标…'):t('恢复目标'),'goal-status primary',()=>this.setStatus('active'),this.busy()));
      }
      this.goalRoot.append(button(this.cancelBusy?t('正在取消目标…'):t('取消目标'),'goal-cancel secondary',()=>this.cancel(),this.busy()));
    }
    if(goalIdentity){
      const close=node('button','×','goal-close');close.type='button';close.title=t('隐藏目标栏');close.setAttribute('aria-label',close.title);
      close.onclick=()=>{this.storage.setItem('hidden-goal:'+this.key,goalIdentity);this.render();this.goalToggle.focus();};
      this.goalRoot.append(close);
    }
  }
}

if(typeof module!=='undefined')module.exports={WorkModes};
