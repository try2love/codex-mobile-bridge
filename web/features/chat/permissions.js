'use strict';
// Shared presentation only; callers retain their own permission/confirmation flow.
function permissionOption(button,preset,label,help,selected){
  button.className='permission-option';button.type='button';
  button.setAttribute('aria-pressed',String(selected));
  const svg=document.createElementNS('http://www.w3.org/2000/svg','svg');
  svg.setAttribute('viewBox','0 0 24 24');svg.setAttribute('aria-hidden','true');
  const path=document.createElementNS(svg.namespaceURI,'path');
  path.setAttribute('d','M12 3 3 7v5c0 5 9 9 9 9s9-4 9-9V7l-9-4Z'+(preset==='ask'?'M12 8v5m0 3h.01':preset==='auto-review'?'M8 12l3 3 5-6':'M13 7l-4 6h4l-2 4 5-6h-4l1-4'));
  svg.append(path);
  const copy=document.createElement('span'),title=document.createElement('strong'),description=document.createElement('small');
  title.textContent=BridgeI18n.t(label);description.textContent=BridgeI18n.t(help);copy.append(title,description);
  const check=document.createElement('span');check.className='permission-check';check.textContent=selected?'✓':'';check.setAttribute('aria-hidden','true');
  button.append(svg,copy,check);return button;
}

// All clients and side chats share native context usage and last-prompt semantics.
class ContextUsageControl {
  constructor({onCompact}={}){
    this.onCompact=onCompact;this.button=document.createElement('button');this.button.type='button';this.button.className='tool-button context-usage';this.button.hidden=true;
    this.ring=document.createElement('span');this.ring.className='context-usage-ring';this.ring.setAttribute('aria-hidden','true');this.button.append(this.ring);
    this.button.onclick=()=>this.open();
  }
  update(state){
    if(!state||state.id!==this.state?.id){this.dialog?.remove();this.dialog=null;}
    this.state=state;if(this.compactButton)this.compactButton.disabled=!state?.connected||state?.capabilities?.compact!==true||!!state?.busy;this.button.hidden=!state;const usage=state?.contextUsage;
    const known=usage?.measurement!=='lastPrompt'&&Number.isFinite(usage?.usedTokens)&&usage.usedTokens>=0&&Number.isFinite(usage.contextWindow)&&usage.contextWindow>0;
    const percent=known?Math.round(usage.usedTokens/usage.contextWindow*100):null;
    this.ring.style.setProperty('--context-percent',known?Math.min(percent,100)+'%':'0%');this.ring.classList.toggle('unknown',!known);
    this.button.title=BridgeI18n.t('上下文使用量')+' · '+(known?percent+'%':BridgeI18n.t('暂未提供'));this.button.setAttribute('aria-label',this.button.title);
  }
  open(){
    if(!this.state)return;this.dialog?.remove();const dialog=this.dialog=document.createElement('dialog');dialog.className='picker';
    const title=document.createElement('h2');title.textContent=BridgeI18n.t('上下文使用量');
    const close=document.createElement('button');close.type='button';close.className='icon-button';close.textContent='×';close.setAttribute('aria-label',BridgeI18n.t('关闭'));close.onclick=()=>dialog.close();const head=document.createElement('div');head.className='picker-head';head.append(title,close);dialog.append(head);
    const line=(label,value)=>{const p=document.createElement('p');p.textContent=BridgeI18n.t(label)+(value===undefined?'':' · '+value);dialog.append(p);};
    const usage=this.state.contextUsage,previous=this.state.lastPromptContextUsage,number=value=>Number.isFinite(value)&&value>=0?value.toLocaleString(BridgeI18n.locale()):BridgeI18n.t('暂未提供');
    if(!usage)line(this.state.contextUsageStatus==='unsupported'?'当前客户端或会话模式未提供上下文用量接口。':this.state.contextUsageStatus==='unavailable'?'当前会话尚未提供上下文用量；有可用数据时会自动更新。':'当前客户端尚未提供此会话的上下文用量。');
    else {
      line(usage.measurement==='lastPrompt'?'最近一次模型输入':'已用 Token',number(usage.usedTokens));line('上下文容量',number(usage.contextWindow));
      if(usage.measurement!=='lastPrompt'&&usage.contextWindow>0&&Number.isFinite(usage.usedTokens)&&usage.usedTokens>=0)line('使用占比',(usage.usedTokens/usage.contextWindow*100).toFixed(1)+'%');
      if(usage.model)line('模型',usage.model);
      if(Number.isFinite(usage.autoCompactThresholdTokens))line('自动压缩阈值',number(usage.autoCompactThresholdTokens));
      if(Number.isFinite(usage.pressureTokens)&&usage.pressureTokens!==usage.usedTokens)line('已确认上下文 Token',number(usage.pressureTokens));
      for(const [key,label] of [['inputTokens','最近输入 Token'],['cacheReadTokens','缓存读取 Token'],['cacheWriteTokens','缓存写入 Token']])if(Number.isFinite(usage.cache?.[key]))line(label,number(usage.cache[key]));
      if(usage.compacted)line('最近一次压缩后统计。');
      if(usage.measurement==='lastPrompt')line('统计最近一次请求的输入与缓存 Token；不包含尚未发送的内容。');
      if(usage.estimated)line('包含客户端对尚未发送内容的估算。');
      if(usage.cached||!this.state.connected)line('当前为缓存数据，连接恢复后更新。');
    }
    if(previous&&!usage){line('最近一次模型输入',number(previous.usedTokens));line('统计最近一次请求的输入与缓存 Token；不包含尚未发送的内容。');}
    if(this.onCompact&&this.state.capabilities?.compact===true){
      const stateId=this.state.id,button=this.compactButton=document.createElement('button');button.type='button';button.textContent=BridgeI18n.t('压缩上下文');button.disabled=!this.state.connected||!!this.state.busy;
      button.onclick=async()=>{if(button.disabled||stateId!==this.state?.id)return;button.disabled=true;try{if(await this.onCompact())dialog.close();}finally{if(dialog.open&&stateId===this.state?.id)button.disabled=!this.state.connected||this.state.capabilities?.compact!==true||!!this.state.busy;}};
      dialog.append(button);
    }
    dialog.addEventListener('close',()=>dialog.remove(),{once:true});document.body.append(dialog);dialog.showModal();
  }
  dispose(){this.dialog?.remove();this.button.remove();}
}

// Linux Desktop and native CLI capability picker; other sessions keep their existing flow.
async function permissionPicker(dialog,{load,save,isCurrent,onSaved,nativeCli=false}){
  const t=BridgeI18n.t,options=document.createElement('div'),status=document.createElement('p'),refresh=document.createElement('button');
  status.className='muted';status.setAttribute('role','status');refresh.type='button';refresh.textContent=t('刷新');
  dialog.append(options,status,refresh);
  let busy=false,revision=0,buttons=[];
  const valid=()=>dialog.open&&isCurrent();
  const setBusy=value=>{busy=value;refresh.disabled=value;for(const [button,available] of buttons)button.disabled=value||!available;};
  const labels={ask:['请求批准','需要额外权限时询问你。'],'auto-review':['帮我批准','由 Codex 审核需要额外权限的操作。'],'full-access':['完全访问权限','允许访问工作区外的文件和网络，无需逐次批准。']};
  const unknown=nativeCli?'无法确认当前 CLI 的权限能力，请检查 CLI 运行时与策略后刷新。':'无法确认当前权限能力，请在桌面检查设置后重新打开此面板。';
  const reload=async()=>{
    if(busy||!valid())return;
    const version=++revision;options.replaceChildren();buttons=[];setBusy(true);status.textContent=t('正在读取权限选项…');
    try{
      const result=await load();if(version!==revision||!valid())return;
      options.replaceChildren();buttons=[];
      for(const option of result.options||[]){
        const preset=option.preset;if(!Object.hasOwn(labels,preset))continue;
        const [label,help]=labels[preset],available=option.available===true;
        const button=permissionOption(document.createElement('button'),preset,label,available?help:option.reason||unknown,result.current===preset);
        buttons.push([button,available]);button.disabled=!available;
        button.onclick=async()=>{
          if(busy||!available||!valid())return;
          if(preset==='full-access'&&!confirm(t('允许此会话完全访问电脑文件和网络？请仅在信任任务内容时开启。')))return;
          setBusy(true);status.textContent='';
          try{
            const response=await save(preset);if(!valid())return;
            if(response.confirmed===true){onSaved(response);dialog.close();}
            else status.textContent=t(nativeCli?'已提交权限设置，等待 CLI 运行时确认。':'已提交权限设置，等待桌面确认。');
          }catch(e){if(valid())status.textContent=t(e.message);}
          finally{setBusy(false);}
        };options.append(button);
      }
      status.textContent=buttons.length?'':t(unknown);
    }catch(e){if(valid())status.textContent=t(e.message);}
    finally{setBusy(false);}
  };
  refresh.onclick=reload;dialog.addEventListener('close',()=>{revision++;});
  await reload();
}
