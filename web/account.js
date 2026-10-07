'use strict';
// Shared by the phone and desktop; only sanitized account data reaches this view.
class AccountPanel {
  constructor({root,button,status=null,read,consume,onHidden=()=>{},visible=()=>false}){
    Object.assign(this,{root,button,status,read,consume,onHidden,visible});
    this.value=null;this.generation=0;this.busy=false;this.loading=false;this.checkedAt=0;
    this.timer=null;this.countdownTimer=null;this.pending=null;this.message='';this.error='';
  }
  clear(){
    clearTimeout(this.timer);
    clearTimeout(this.countdownTimer);this.countdownTimer=null;
    this.loading=false;
    this.generation++;this.value=null;this.pending=null;this.checkedAt=0;
    this.root.replaceChildren();this.button.hidden=true;this.onHidden();
    if(this.status)this.status.hidden=true;
  }
  schedule(){
    clearTimeout(this.timer);
    if(this.visible())this.timer=setTimeout(()=>{if(this.visible())this.refresh(false);},Math.max(0,300000-(Date.now()-this.checkedAt)));
    if(!this.visible()){clearTimeout(this.countdownTimer);this.countdownTimer=null;}
    else if(!this.countdownTimer)this.countdownTimer=setTimeout(()=>{this.countdownTimer=null;if(this.visible()){this.render();this.schedule();}},60000);
  }
  async refresh(refresh=true){
    if(this.loading||this.busy)return;
    const generation=this.generation;this.loading=true;this.render();
    try{
      const value=await this.read(refresh);
      if(generation!==this.generation)return;
      this.error='';this.accept(value);this.checkedAt=Date.now();
    }catch(error){
      if(generation!==this.generation)return;
      // Retain a labeled snapshot, but never authorize a write from stale identity data.
      this.checkedAt=Date.now();this.error=error.message;
      if(this.value?.visible)this.value={...this.value,canReset:false,error:BridgeI18n.t('保留上次结果，数据尚未更新')};
      else{this.value=null;this.root.replaceChildren(this.node('p',BridgeI18n.t('登录状态暂不可用'),'error'));}
    }finally{if(generation===this.generation){this.loading=false;this.render();this.schedule();}}
  }
  accept(value){
    if(!value?.visible){
      this.value=value;this.pending=null;this.message='';this.error='';
      this.root.replaceChildren();this.button.hidden=true;this.onHidden();this.render();return;
    }
    if(this.value?.accountKey!==value.accountKey){this.pending=null;this.message='';this.error='';}
    this.value=value;this.button.hidden=false;
    this.pending=value.pendingReset||null;
    this.render();
  }
  node(tag,text,cls){const n=document.createElement(tag);if(text!==undefined)n.textContent=text;if(cls)n.className=cls;return n;}
  time(value){return typeof value==='number'?new Date(value*1000).toLocaleString(BridgeI18n.locale()):BridgeI18n.t('暂未提供');}
  duration(minutes){
    const t=BridgeI18n.t;
    if(typeof minutes!=='number'||minutes<=0)return t('额度窗口');
    if(minutes%1440===0)return minutes/1440+t(' 天');
    if(minutes%60===0)return minutes/60+t(' 小时');
    return minutes+t(' 分钟');
  }
  countdown(minutes){
    const t=BridgeI18n.t,days=Math.floor(minutes/1440),hours=Math.floor(minutes%1440/60),rest=minutes%60;
    return [days?days+t(' 天'):'',hours?hours+t(' 小时'):'',rest?rest+t(' 分钟'):''].filter(Boolean).join(' ');
  }
  render(){
    const value=this.value;
    if(this.status){
      this.status.hidden=!!value?.visible||(!value&&!this.loading);
      this.status.textContent=BridgeI18n.t(this.loading&&!value?.visible?'正在读取登录状态…':
        ({api:'API 接入',signedOut:'Codex 未登录',unknown:'登录状态未识别',unavailable:'登录状态暂不可用'}[value?.loginType]||'登录状态暂不可用'));
    }
    if(!value?.visible)return;
    const t=BridgeI18n.t,root=this.root;root.replaceChildren();
    const heading=this.node('div',undefined,'account-heading'),refresh=this.node('button',t('查看剩余额度'));
    refresh.type='button';refresh.disabled=this.loading||this.busy;
    refresh.onclick=()=>this.refresh();
    heading.append(this.node('strong',value.email||t('ChatGPT 账号')),refresh);root.append(heading);
    if(this.loading){const query=this.node('p',t('查询中…'),'account-query');query.setAttribute('role','status');root.append(query);}
    root.append(this.node('p',t('此电脑 · 账号共享额度')+(value.planType?' · '+value.planType:''),'account-muted'));
    if(value.error)root.append(this.node('p',t(value.error),'error'));
    if(!value.limits.length&&!value.error)root.append(this.node('p',t('暂未提供额度信息'),'account-muted'));
    for(const bucket of value.limits){
      const card=this.node('section',undefined,'account-quota');card.append(this.node('h3',bucket.name));
      if(!bucket.windows.length)card.append(this.node('p',t('暂未提供额度信息'),'account-muted'));
      for(const window of bucket.windows){
        const row=this.node('div',undefined,'account-window');
        row.append(this.node('span',this.duration(window.windowDurationMins)),this.node('strong',window.remainingPercent==null?t('暂未提供'):t('剩余 ')+Math.round(window.remainingPercent*10)/10+'%'));
        card.append(row);
        if(window.remainingPercent!=null){const progress=this.node('progress');progress.max=100;progress.value=window.remainingPercent;progress.setAttribute('aria-label',t('剩余额度'));card.append(progress);}
        let reset=t('恢复时间：')+this.time(window.resetsAt);
        if(typeof window.resetsAt==='number'){
          const minutes=Math.ceil((window.resetsAt*1000-Date.now())/60000);
          reset+=' · '+(minutes>0?t('约 ')+this.countdown(minutes)+t('后恢复'):t('等待刷新'));
        }
        card.append(this.node('p',reset,'account-muted'));
      }
      if(bucket.credits)card.append(this.node('p','Credits: '+(bucket.credits.unlimited?t('不限量'):bucket.credits.balance??t('暂未提供')),'account-muted'));
      root.append(card);
    }
    const period=value.subscription?.periodEndsAt;
    if(period){const minutes=Math.ceil((period*1000-Date.now())/60000);root.append(this.node('p',t('当前订阅周期：')+(minutes>0?this.countdown(minutes)+t('后结束'):t('已到记录日期，请核对续费状态'))+' · '+this.time(period),'account-subscription'));}
    else root.append(this.node('p',t('订阅周期暂未提供'),'account-muted'));
    const cards=value.resetCredits,section=this.node('section',undefined,'account-cards');
    section.append(this.node('h3',t('重置卡')));
    section.append(this.node('p',cards?.availableCount==null?t('暂未提供重置卡信息'):t('可用数量：')+cards.availableCount));
    if(!value.canReset&&!value.error)section.append(this.node('p',t('请先在 Codex 桌面设置中允许使用额度重置'),'account-muted'));
    if(this.pending){
      section.append(this.node('p',t('上次重置结果尚未确认，请重试原请求。')));
      const retry=this.node('button',t('重试原请求'));retry.type='button';retry.disabled=this.busy||this.loading||!value.canReset;
      retry.onclick=()=>this.redeem(this.pending.creditId,true);section.append(retry);
    }else if(cards?.availableCount>0){
      if(cards.credits===null){
        const button=this.node('button',t('使用一张重置卡'));button.type='button';button.disabled=this.busy||this.loading||!value.canReset||!!value.error;
        button.onclick=()=>this.redeem(null);section.append(button);
      }else{
        for(const card of cards.credits||[]){
          const row=this.node('div',undefined,'account-card');
          row.append(this.node('strong',card.title||t('重置卡')));
          if(card.description)row.append(this.node('p',card.description,'account-muted'));
          row.append(this.node('p',card.expiresAt==null?t('未提供到期时间'):t('到期时间：')+this.time(card.expiresAt),'account-muted'));
          const button=this.node('button',t('使用重置卡'));button.type='button';
          button.disabled=this.busy||this.loading||!value.canReset||!!value.error||card.status!=='available'||card.resetType!=='codexRateLimits'||(card.expiresAt!=null&&card.expiresAt*1000<=Date.now());
          button.onclick=()=>this.redeem(card.id);row.append(button);section.append(row);
        }
        if(!cards.credits.length)section.append(this.node('p',t('卡片详情暂不可用，请刷新后重试'),'account-muted'));
      }
    }
    root.append(section);
    if(this.message){const message=this.node('p',t(this.message));message.setAttribute('role','status');root.append(message);}
    if(this.error){const error=this.node('p',t(this.error),'error');error.setAttribute('role','alert');root.append(error);}
    root.append(this.node('p',t('更新于：')+this.time(value.updatedAt),'account-muted'));
  }
  requestId(){if(typeof crypto.randomUUID==='function')return crypto.randomUUID();const bytes=crypto.getRandomValues(new Uint8Array(16));bytes[6]=(bytes[6]&15)|64;bytes[8]=(bytes[8]&63)|128;const h=[...bytes].map(n=>n.toString(16).padStart(2,'0')).join('');return h.slice(0,8)+'-'+h.slice(8,12)+'-'+h.slice(12,16)+'-'+h.slice(16,20)+'-'+h.slice(20);}
  async redeem(creditId,retry=false){
    const value=this.value;if(this.busy||this.loading||!value?.visible||!value.canReset||value.error)return;
    const t=BridgeI18n.t;
    const card=value.resetCredits?.credits?.find(row=>row.id===creditId);
    const prompt=(retry?t('确认重试同一次重置？'):t('确认使用一张重置卡？'))+'\n'+
      (value.email||t('ChatGPT 账号'))+'\n'+(card?.title||t('重置卡'))+'\n'+t('成功后会消耗一张卡，重置符合条件的账号额度。');
    if(!window.confirm(prompt))return;
    const attempt=retry?this.pending:{requestId:this.requestId(),accountKey:value.accountKey,creditId};
    if(!attempt)return;
    this.pending=attempt;this.busy=true;this.message='';this.error='';this.render();
    const generation=this.generation;
    try{
      const result=await this.consume({...attempt,confirmed:true});
      if(generation!==this.generation)return;
      this.pending=null;
      this.accept(result.account);
      if(this.value?.accountKey!==attempt.accountKey)return;
      this.message={reset:'已使用重置卡，额度已刷新',alreadyRedeemed:'此重置已完成，额度已刷新',nothingToReset:'当前没有可重置的额度窗口',noCredit:'当前没有可用重置卡'}[result.outcome]||'请刷新查看重置结果';
    }catch(error){
      if(generation!==this.generation)return;
      this.error=error.message;
      // Fetch the persisted attempt, retaining its key if the refresh also fails.
      try{const latest=await this.read();if(generation===this.generation){this.pending=null;this.accept(latest);}}
      catch{if(generation===this.generation)this.clear();}
    }finally{this.busy=false;if(generation===this.generation)this.render();}
  }
}
if(typeof module!=='undefined')module.exports={AccountPanel};
