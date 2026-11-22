'use strict';
// Shared presentation for provider-supplied quota windows. Display never authorizes a write.
const QuotaDisplay=(()=>{
  const t=value=>typeof BridgeI18n==='undefined'?value:BridgeI18n.t(value);
  const node=(tag,cls,text)=>{const value=document.createElement(tag);if(cls)value.className=cls;if(text!==undefined)value.textContent=text;return value;};
  const finite=value=>typeof value==='number'&&Number.isFinite(value);
  function windows(rows){
    return (Array.isArray(rows)?rows:[]).filter(row=>row&&typeof row==='object').map(row=>{
      const duration=finite(row.windowDurationMins)&&row.windowDurationMins>0?row.windowDurationMins:({'5h':300,weekly:10080,'7d':10080,daily:1440,'1d':1440}[row.window]||null);
      const percent=finite(row.remainingPercent)&&row.remainingPercent>=0&&row.remainingPercent<=100?Math.round(row.remainingPercent*10)/10:null;
      return {...row,key:duration?'duration:'+duration:String(row.kind||row.window||row.id||'quota'),duration,percent,
        reset:finite(row.resetsAt)?row.resetsAt:finite(row.resetAt)?row.resetAt:null};
    }).sort((a,b)=>(a.duration??Infinity)-(b.duration??Infinity));
  }
  function label(row){const minutes=row.duration;if(minutes)return minutes%1440===0?minutes/1440+'d':minutes%60===0?minutes/60+'h':minutes+'m';return row.label||t('额度窗口');}
  function columns(sets){const seen=new Map();for(const rows of sets)for(const row of windows(rows))if(!seen.has(row.key))seen.set(row.key,row);return [...seen.values()].sort((a,b)=>(a.duration??Infinity)-(b.duration??Infinity));}
  function remaining(row){return row.percent===null?t('暂未提供'):t('剩余 ')+row.percent+'%';}
  function quantity(row){return finite(row.remaining)?[row.remaining,row.total].filter(finite).map(n=>n.toLocaleString()).join(' / ')+(row.unit?' '+row.unit:''):'';}
  function time(seconds){return finite(seconds)?new Date(seconds*1000).toLocaleString(typeof BridgeI18n==='undefined'?'zh-CN':BridgeI18n.locale()):t('暂未提供');}
  function countdown(row){if(!finite(row.reset))return '';const mins=Math.ceil((row.reset*1000-Date.now())/60000);if(mins<=0)return t('等待刷新');const days=Math.floor(mins/1440),hours=Math.floor(mins%1440/60),rest=mins%60;return t('约 ')+[days?days+'d':'',hours?hours+'h':'',rest?rest+'m':''].filter(Boolean).join(' ')+t('后恢复');}
  function description(row){return [label(row),remaining(row),quantity(row),row.reset?t('恢复时间：')+time(row.reset):row.resetNote||''].filter(Boolean).join(' · ');}
  function badges(rows,{compact=false,slots=null,stale=false}={}){
    const values=windows(rows),layout=slots?.length?slots:values,root=node('span','quota-badges'+(compact?' is-compact':'')+(stale?' is-stale':''));
    root.style?.setProperty('--quota-columns',String(Math.max(1,layout.length)));
    for(const column of layout){
      const row=values.find(value=>value.key===column.key),chip=node('span','quota-chip'+(!row?' is-placeholder':''));
      if(!row){chip.setAttribute('aria-hidden','true');root.append(chip);continue;}
      const tone=row.percent===null?'unknown':row.percent<=0?'exhausted':row.percent<20?'warning':'good';
      const ring=node('span','quota-pie-ring '+tone);ring.style?.setProperty('--quota-pct',String(row.percent??0));ring.setAttribute('aria-hidden','true');
      chip.title=description(row);chip.setAttribute('aria-label',chip.title);chip.append(ring);
      if(!compact)chip.append(node('span','quota-window-label',label(row)),node('span','quota-window-percent',row.percent===null?'—':row.percent+'%'));
      root.append(chip);
    }
    return root;
  }
  function details(rows){
    const root=node('div','quota-details');
    for(const row of windows(rows)){
      const line=node('div','quota-detail');line.append(node('strong','',label(row)+' · '+remaining(row)));
      if(row.percent!==null)line.append(node('span','',t('已用')+' '+Math.round((100-row.percent)*10)/10+'%'));
      if(quantity(row))line.append(node('span','',quantity(row)));
      line.append(node('small','',row.reset?t('恢复时间：')+time(row.reset)+(countdown(row)?' · '+countdown(row):''):row.resetNote||t('恢复时间：')+t('暂未提供')));root.append(line);
    }
    return root;
  }
  function pools(groups,{slots=null,stale=false}={}){
    slots=columns([...(groups||[]).map(group=>group.windows||group.buckets||[]),slots||[]]);
    const root=node('div','quota-pools');
    for(const group of groups||[]){
      const rows=group.windows||group.buckets||[],card=node('details','quota-pool'),head=node('summary','quota-pool-heading');
      card.dataset&&(card.dataset.quotaKey=group.id||group.name||'');
      head.append(node('span','quota-pool-name',group.name||t('官方账号额度')),badges(rows,{slots,stale}));
      const reset=windows(rows).map(row=>[label(row),countdown(row)].filter(Boolean).join(' ')).filter(Boolean).join(' · ');
      if(reset)head.append(node('small','quota-pool-reset',reset));card.append(head,details(rows));
      if(group.models?.length)card.append(node('p','quota-members',t('共用模型：')+group.models.join('、')));
      root.append(card);
    }
    return root;
  }
  function summary(root,value,{slots=null,models=[]}={}){
    const opened=new Set([...(root.querySelectorAll?.('.quota-pool')||[])].filter(row=>row.open).map(row=>row.dataset.quotaKey));
    root.replaceChildren();root.className='quota-summary';
    if(value?.tier)root.append(node('strong','quota-tier',value.tier));
    if(value?.groups?.length)root.append(pools(value.groups,{slots,stale:value.status==='stale'}));
    else for(const item of value?.items||[])if(!models.some(row=>row.name===item.name&&row.quotaTag))root.append(node('p','quota-legacy',[item.name,item.val,item.note].filter(Boolean).join(' · ')));
    if(value?.credits&&!value?.groups?.length)root.append(node('p','quota-credits',value.credits));
    if(value?.status==='stale')root.append(node('p','quota-status',t('保留上次结果，数据尚未更新')));
    if(value?.checkedAt)root.append(node('small','quota-updated',t('额度更新于：')+time(value.checkedAt)));
    for(const row of root.querySelectorAll?.('.quota-pool')||[])row.open=opened.has(row.dataset.quotaKey);
    return root.children.length>0;
  }
  function tool(open){
    const button=node('button','tool-button remaining-quota');button.type='button';button.hidden=true;button.disabled=true;button.onclick=open;button.setAttribute('aria-haspopup','dialog');
    button.quotaLabel=node('span','quota-tool-label',t('剩余额度'));button.quotaValues=node('span','quota-tool-values');button.append(button.quotaLabel,button.quotaValues);return button;
  }
  function renderTool(button,rows,{stale=false}={}){
    const values=windows(rows);button.dataset.hasQuota=String(values.length>0);button.quotaLabel.textContent=t('剩余额度');button.quotaValues.replaceChildren(badges(rows,{stale}));
    button.title=[t('剩余额度'),...values.map(description),stale?t('保留上次结果，数据尚未更新'):''].filter(Boolean).join('\n');button.setAttribute('aria-label',button.title);
  }
  function accountWindows(value){if(!value?.visible||!Array.isArray(value.limits))return [];const bucket=value.limits.find(row=>row.id==='codex')||(value.limits.length===1?value.limits[0]:null);return bucket?.windows||[];}
  return {windows,label,columns,badges,details,pools,summary,tool,renderTool,accountWindows,description,time,t};
})();

class AccountQuotaIndicator {
  constructor({read,context,visible,open=()=>{},onChange=()=>{},now=()=>Date.now()}){
    Object.assign(this,{read,context,visible,onChange,now});this.key=null;this.generation=0;this.value=null;this.loading=false;this.checkedAt=0;this.retryAt=0;this.error='';
    this.root=QuotaDisplay.tool(open);
  }
  clear(){this.generation++;this.key=null;this.value=null;this.checkedAt=0;this.retryAt=0;this.loading=false;this.error='';this.render();}
  async refresh(force=false){
    const context=this.context();if(!context){this.clear();return;}
    if(context.key!==this.key){this.clear();this.key=context.key;}
    if(!this.visible()){this.root.hidden=true;return;}this.render();
    if(this.loading||!force&&(this.error?this.now()<this.retryAt:this.checkedAt&&this.now()-this.checkedAt<300000))return;
    const generation=this.generation,key=this.key;this.loading=true;this.render();
    try{
      const value=await this.read(context,force);if(generation!==this.generation||this.context()?.key!==key)return;
      // Never retain a previous account's numbers when a failed reply identifies a different account.
      if(this.value?.accountKey&&value?.accountKey!==this.value.accountKey)this.value=null;
      this.error=value?.error||'';if(!this.error||!this.value)this.value=value;
      if(!this.error)this.checkedAt=this.now();this.retryAt=this.error?this.now()+60000:0;
    }catch(error){if(generation!==this.generation||this.context()?.key!==key)return;this.error=error.message||QuotaDisplay.t('额度暂不可用');this.retryAt=this.now()+60000;}
    finally{if(generation===this.generation){this.loading=false;this.render();}}
  }
  render(){
    const rows=QuotaDisplay.accountWindows(this.value);this.root.hidden=!this.value?.visible||!this.key||this.context()?.key!==this.key||!this.visible();
    this.root.disabled=!this.value?.visible||!this.key||this.context()?.key!==this.key;
    QuotaDisplay.renderTool(this.root,rows,{stale:!!this.error});this.onChange(this.value,this.error,this.loading);
  }
}
// Independent containers reuse presentation; the owning view retains query and identity state.
class QuotaDetailsDialog {
  constructor({refresh,onClose=()=>{}}){
    const make=(tag,cls,text)=>{const node=document.createElement(tag);node.className=cls;if(text!==undefined)node.textContent=text;return node;};
    this.root=make('dialog','picker quota-details-dialog');this.root.setAttribute('aria-label',QuotaDisplay.t('剩余额度'));
    const head=make('div','picker-head');this.title=make('h2','',QuotaDisplay.t('剩余额度'));const close=make('button','icon-button','×');close.type='button';close.setAttribute('aria-label',QuotaDisplay.t('关闭'));close.onclick=()=>this.close();head.append(this.title,close);
    this.content=make('div','quota-summary');this.error=make('p','error');this.error.setAttribute('role','status');this.refresh=make('button','tool-button',QuotaDisplay.t('刷新额度'));this.refresh.type='button';this.refresh.onclick=refresh;this.root.append(head,this.content,this.error,this.refresh);this.root.addEventListener('close',onClose);document.body.append(this.root);
  }
  render(value,{loading=false,error='',models=[]}={}){
    this.title.textContent=QuotaDisplay.t('剩余额度');this.root.setAttribute('aria-label',this.title.textContent);QuotaDisplay.summary(this.content,value,{models});
    if(!this.content.children.length){const text=document.createElement('p');text.className='account-muted';text.textContent=QuotaDisplay.t(loading?'正在读取额度…':'暂未提供额度信息');this.content.append(text);}
    this.error.textContent=error;this.error.hidden=!error;this.refresh.textContent=QuotaDisplay.t('刷新额度');this.refresh.disabled=loading;
  }
  show(){this.root.showModal();}
  close(){if(this.root.open)this.root.close();}
  dispose(){this.close();this.root.remove();}
}
if(typeof module!=='undefined')module.exports={QuotaDisplay,AccountQuotaIndicator,QuotaDetailsDialog};
