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

// Both Codex composers use fresh capabilities; other clients keep permissionOption.
async function permissionPicker(dialog,{load,save,isCurrent,onSaved}){
  const t=BridgeI18n.t,options=document.createElement('div'),status=document.createElement('p'),refresh=document.createElement('button');
  status.className='muted';status.setAttribute('role','status');refresh.type='button';refresh.textContent=t('刷新');
  dialog.append(options,status,refresh);
  let busy=false,revision=0,buttons=[];
  const valid=()=>dialog.open&&isCurrent();
  const setBusy=value=>{busy=value;refresh.disabled=value;for(const [button,available] of buttons)button.disabled=value||!available;};
  const labels={ask:['请求批准','需要额外权限时询问你。'],'auto-review':['帮我批准','由 Codex 审核需要额外权限的操作。'],'full-access':['完全访问权限','允许访问工作区外的文件和网络，无需逐次批准。']};
  const reload=async()=>{
    if(busy||!valid())return;
    const version=++revision;options.replaceChildren();buttons=[];setBusy(true);status.textContent=t('正在读取权限选项…');
    try{
      const result=await load();if(version!==revision||!valid())return;
      options.replaceChildren();buttons=[];
      for(const option of result.options||[]){
        const preset=option.preset;if(!Object.hasOwn(labels,preset))continue;
        const [label,help]=labels[preset],available=option.available===true;
        const button=permissionOption(document.createElement('button'),preset,label,available?help:option.reason||'无法确认当前权限能力，请在桌面检查设置后重新打开此面板。',result.current===preset);
        buttons.push([button,available]);button.disabled=!available;
        button.onclick=async()=>{
          if(busy||!available||!valid())return;
          if(preset==='full-access'&&!confirm(t('允许此会话完全访问电脑文件和网络？请仅在信任任务内容时开启。')))return;
          setBusy(true);status.textContent='';
          try{
            const response=await save(preset);if(!valid())return;
            if(response.confirmed===true){onSaved(response);dialog.close();}
            else status.textContent=t('已提交权限设置，等待桌面确认。');
          }catch(e){if(valid())status.textContent=t(e.message);}
          finally{setBusy(false);}
        };options.append(button);
      }
      status.textContent=buttons.length?'':t('无法确认当前权限能力，请在桌面检查设置后重新打开此面板。');
    }catch(e){if(valid())status.textContent=t(e.message);}
    finally{setBusy(false);}
  };
  refresh.onclick=reload;dialog.addEventListener('close',()=>{revision++;});
  await reload();
}
