'use strict';

function accountQuotaWindows(value){
  if(!value?.visible||!Array.isArray(value.limits))return [null,null];
  const bucket=value.limits.find(row=>row.id==='codex')||(value.limits.length===1?value.limits[0]:null);
  return ['primary','secondary'].map(kind=>{
    const row=bucket?.windows?.find(window=>window.kind===kind);
    if(!row)return null;
    const percent=typeof row.remainingPercent==='number'&&Number.isFinite(row.remainingPercent)
      &&row.remainingPercent>=0&&row.remainingPercent<=100?Math.round(row.remainingPercent*10)/10:null;
    return {...row,percent};
  });
}

class AccountQuotaRings {
  constructor({read,context,visible,document:doc=globalThis.document,now=()=>Date.now()}){
    Object.assign(this,{read,context,visible,doc,now});
    this.key=null;this.generation=0;this.value=null;this.loading=false;this.checkedAt=0;this.retryAt=0;this.error='';
    this.root=this.node('div','account-quota-rings');this.root.hidden=true;
    this.root.setAttribute('role','group');
    this.rings=['primary','secondary'].map((kind,index)=>{
      const button=this.node('button','account-quota-button');button.type='button';button.dataset.kind=kind;
      const gauge=this.node('span','account-quota-gauge'),percent=this.node('span','account-quota-percent');
      const label=this.node('span','account-quota-label');gauge.append(percent);button.append(gauge,label);
      button.setAttribute('aria-haspopup','dialog');button.onclick=()=>this.openDetails(index);
      this.root.append(button);return {button,gauge,percent,label};
    });
  }
  t(text){return typeof BridgeI18n==='undefined'?text:BridgeI18n.t(text);}
  node(tag,className,text){const node=this.doc.createElement(tag);if(className)node.className=className;if(text!==undefined)node.textContent=text;return node;}
  label(window,index){
    const minutes=window?.windowDurationMins;
    if(minutes===10080)return this.t('每周');
    if(Number.isFinite(minutes)&&minutes>0){
      if(minutes%1440===0)return minutes/1440+this.t('天');
      if(minutes%60===0)return minutes/60+this.t('小时');
      return minutes+this.t('分钟');
    }
    return this.t(index===0?'短期':'每周');
  }
  date(seconds){
    if(!Number.isFinite(seconds))return this.t('暂未提供');
    const locale=typeof BridgeI18n==='undefined'?'zh-CN':BridgeI18n.locale();
    return new Date(seconds*1000).toLocaleString(locale,{month:'numeric',day:'numeric',hour:'2-digit',minute:'2-digit'});
  }
  clear(){
    this.generation++;this.key=null;this.value=null;this.checkedAt=0;this.retryAt=0;this.loading=false;this.error='';
    this.root.hidden=true;this.dialog?.close();
  }
  async refresh(force=false){
    const context=this.context();
    if(!context){this.clear();return;}
    if(context.key!==this.key){this.clear();this.key=context.key;}
    if(!this.visible()){this.root.hidden=true;this.dialog?.close();return;}
    this.render();
    if(this.loading||!force&&(this.error?this.now()<this.retryAt:this.checkedAt&&this.now()-this.checkedAt<300000))return;
    const generation=this.generation,key=this.key;this.loading=true;this.render();
    try{
      const value=await this.read(context,force);
      if(generation!==this.generation||this.context()?.key!==key)return;
      this.value=value;this.error=value?.error||'';this.checkedAt=this.now();this.retryAt=this.error?this.now()+60000:0;
    }catch(error){
      if(generation!==this.generation||this.context()?.key!==key)return;
      this.error=error.message||this.t('额度暂不可用');this.retryAt=this.now()+60000;
    }finally{if(generation===this.generation){this.loading=false;this.render();}}
  }
  message(){
    if(this.loading)return this.t('正在读取额度…');
    if(this.error)return this.t('数据未更新')+' · '+this.error;
    if(this.value?.loginType==='api')return this.t('API 接入不提供官方额度');
    if(this.value?.loginType==='signedOut')return this.t('Codex 未登录');
    if(!this.value?.visible)return this.t('额度暂不可用');
    return this.t('此电脑 · 账号共享额度');
  }
  render(){
    this.root.hidden=!this.key||this.context()?.key!==this.key||!this.visible();
    this.root.setAttribute('aria-label',this.t('账号剩余额度'));
    this.root.setAttribute('aria-busy',String(this.loading));
    this.root.classList.toggle('stale',!!this.error);
    this.windows=accountQuotaWindows(this.value);
    this.rings.forEach((ring,index)=>{
      const window=this.windows[index],label=this.label(window,index),value=window?.percent;
      ring.label.textContent=label;ring.percent.textContent=value==null?'—':value+'%';
      ring.button.classList.toggle('unknown',value==null);ring.button.classList.toggle('low',value!=null&&value<=10);
      ring.gauge.style.setProperty('--quota-percent',(value??0)+'%');
      const description=label+' · '+(value==null?this.t('暂未提供'):this.t('剩余 ')+value+'%');
      ring.button.setAttribute('aria-label',description);ring.button.title=description+' · '+this.message();
    });
    this.renderDetails();
  }
  openDetails(index){
    this.selected=index;
    if(!this.dialog){
      this.dialog=this.node('dialog','picker account-quota-dialog');
      const heading=this.node('div','picker-head');this.detailsTitle=this.node('h2');
      const close=this.node('button','icon-button','×');close.type='button';close.setAttribute('aria-label',this.t('关闭'));close.onclick=()=>this.dialog.close();
      heading.append(this.detailsTitle,close);
      this.detailsValue=this.node('strong','account-quota-detail-value');this.detailsUsage=this.node('p','muted');
      this.detailsReset=this.node('p','muted');this.detailsStatus=this.node('p','muted');this.detailsUpdated=this.node('small','muted');
      this.refreshButton=this.node('button','primary');this.refreshButton.type='button';this.refreshButton.onclick=()=>this.refresh(true);
      this.dialog.append(heading,this.detailsValue,this.detailsUsage,this.detailsReset,this.detailsStatus,this.detailsUpdated,this.refreshButton);
      this.doc.body.append(this.dialog);
    }
    this.renderDetails();if(!this.dialog.open)this.dialog.showModal();
  }
  renderDetails(){
    if(!this.dialog)return;
    const window=this.windows?.[this.selected],value=window?.percent;
    this.detailsTitle.textContent=this.label(window,this.selected)+' · '+this.t('额度使用情况');
    this.detailsValue.textContent=value==null?'—':value+'%';
    this.detailsUsage.textContent=value==null?this.t('暂未提供额度信息'):this.t('剩余额度')+' '+value+'% · '+this.t('已用')+' '+(Math.round((100-value)*10)/10)+'%';
    this.detailsReset.textContent=this.t('恢复时间：')+this.date(window?.resetsAt);
    this.detailsStatus.textContent=this.message();
    this.detailsUpdated.textContent=this.value?.updatedAt?this.t('上次更新：')+this.date(this.value.updatedAt):'';
    this.refreshButton.textContent=this.t('刷新额度');this.refreshButton.disabled=this.loading;
  }
}
if(typeof module!=='undefined')module.exports={AccountQuotaRings,accountQuotaWindows};
