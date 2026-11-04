'use strict';
// One identity for navigation, drafts and operations; family only selects a protocol.
const ClientIdentity=(()=>{
  const entry=id=>typeof id==='string'&&/^(claude|deepseek):[0-9a-f]{32}$/.test(id);
  const family=id=>entry(id)?id.split(':')[0]:typeof id==='string'&&/^wsl-claude:[0-9a-f]{32}$/.test(id)?'claude':typeof id==='string'&&/^(codex|wsl):[0-9a-f]{32}$/.test(id)?'codex':id;
  const desktop=id=>entry(id)||['claude','deepseek','zcode'].includes(id);
  function path(id,action,sessionId=null,extra={}){
    if(!desktop(id))throw Error('未知客户端接入');
    const query=new URLSearchParams(extra);
    if(sessionId)query.set('sessionId',sessionId);
    if(entry(id))query.set('entry',id);
    return '/api/desktop-sessions/'+family(id)+'/'+action+(query.toString()?'?'+query:'');
  }
  return {entry,family,desktop,path};
})();
const ClientLifecycle=(()=>{
  let sequence=0;
  function connectionModes(client){return Array.isArray(client?.connectionModes)?client.connectionModes.filter(mode=>typeof mode?.id==='string'&&mode.id&&typeof mode.label==='string'):[];}
  function connectionModeLabel(client){return connectionModes(client).find(mode=>mode.id===client.connectionMode)?.label||'';}
  function connection(client={}){
    const stage=client.connectionState,setup=client.setupStatus;
    const waiting=['starting','connecting'].includes(stage)||(!stage&&['starting','connecting'].includes(setup))||
      (client.initializationMode!=='background'&&['needs-screen-saver','needs-unlock','needs-desktop'].includes(setup));
    const background=client.backgroundRunning&&client.mainRunning===false,ready=client.connected===true||setup==='session-access'||client.pipeReady===true;
    let label,reason=client.reason||'',pending=false,action=false;
    if(setup==='recovery-required'||(background&&!waiting&&!client.pendingEnable))label=background?'后台运行，桌面未打开':'需要恢复连接';
    else if(ready)label='已连接';
    else if(client.pendingEnable){label='正在启动应用…';reason='正在等待客户端连接，完成后会自动显示聊天。';pending=true;}
    else if(waiting){label=({'needs-screen-saver':'等待退出屏保','needs-unlock':'等待解锁','needs-desktop':'等待桌面恢复'})[setup]||
      (stage==='starting'?'正在启动应用…':'正在连接，请稍候…');pending=true;}
    else{
      label=({'needs-unlock':'电脑已锁定','needs-desktop':'电脑桌面暂不可用'})[setup]||({'error':'连接失败','timeout':'连接超时','needs-initialization':'需要初始化连接'})[stage]
        ||({'history-only':'本机历史（只读）','unsupported':'暂不支持自动接入','needs-initialization':'需要初始化连接','needs-first-launch':'需要完成首次设置','restart-required':'等待重启','needs-permission':'等待授权','needs-developer-mode':'等待开发者模式确认','needs-trust':'等待目录授权','owner-conflict':'连接被其他网关占用','cancelled':'已取消连接','failed':'连接失败','needs-retry':'连接失败'})[setup];
      action=!!label;
      label=label||(client.installed===false?'未安装':client.running===true?'应用运行中，尚未连接':client.running===false?'应用未运行':'尚未连接');
    }
    const canStartDesktop=client.id==='claude'&&client.canStartDesktop===true&&!client.connected&&!pending;
    const canReconnect=client.id==='claude'&&client.canReconnect===true&&client.reconnectSupported===true&&!client.connected&&!pending&&!['recovery-required','restart-required'].includes(setup);
    const pipe=client.id==='zcode'&&client.pipeSupported===true;
    const canInitialize=client.id==='zcode'
      ?pipe&&client.canInitializeDesktop===true&&client.installed!==false&&!ready&&!pending
      :client.id==='claude'&&!canStartDesktop&&client.enabled&&client.installed!==false&&!client.connected&&!pending&&(['needs-initialization','needs-retry','needs-developer-mode','needs-trust','needs-permission','needs-unlock','needs-desktop','cancelled','failed'].includes(setup)||stage==='needs-initialization'||client.canInitializeDesktop===true&&!canReconnect);
    const initializeLabel=canReconnect?'重新初始化连接':'初始化连接';
    const retryable=client.retryable===true&&client.enabled&&!ready&&!pending&&!canInitialize&&!canReconnect&&!canStartDesktop&&!['recovery-required','restart-required'].includes(setup);
    return {label,modeLabel:connectionModeLabel(client),reason,ready,waiting:pending,action:action||setup==='recovery-required',retryable:!!retryable,canReconnect:!!canReconnect,canInitialize:!!canInitialize,initializeLabel,canStartDesktop:!!canStartDesktop,error:!pending&&!ready&&(['error','timeout'].includes(stage)||setup==='failed')};
  }
  function request(operation,{timeout=45000,uncertain=true,message='操作结果尚未确认，请刷新状态后再重试。'}={}){
    const controller=typeof AbortController!=='undefined'?new AbortController():null;let timer;
    return new Promise((resolve,reject)=>{
      timer=setTimeout(()=>{reject(Object.assign(Error(message),{uncertain}));controller?.abort();},timeout);
      try{Promise.resolve(operation(controller?.signal)).then(resolve,reject);}catch(error){reject(error);}
    }).finally(()=>clearTimeout(timer));
  }
  function failure(action,error,transport='web'){
    const uncertain=!!error.uncertain||(transport==='web'?!(error.status>=400&&error.status<500):/本机操作超时|本机管理进程已退出|本机管理已关闭/.test(error.message));
    return {action,message:error.message,uncertain};
  }
  function failureMessage(value){return value.uncertain?'操作结果尚未确认，请刷新状态后再重试。':value.message;}
  function retryLabel(action){return ({enable:'重试开启',quit:'重试退出',force:'重试后台强制结束',disable:'重试停用',initialize:'重试初始化'})[action]||'重试操作';}
  function reconcile(failures,clients){
    for(const [id,value] of failures||[]){
      if(!value.uncertain)continue;const client=clients.find(row=>row.id===id);
      const ready=client&&(client.connected||client.setupStatus==='session-access');
      const initialized=id==='zcode'?value.connectionMode==='private-pipe'&&(client?.pipeReady===true||ready&&client.pipeSupported===true):client&&(client.connected||connection(client).waiting);
      const completed=client&&(['quit','force'].includes(value.action)?client.enabled===false&&client.running===false:value.action==='disable'?client.enabled===false:value.action==='initialize'?initialized:value.action==='enable'?(!value.connectionMode||client.connectionMode===value.connectionMode)&&client.enabled&&(client.running||client.connected||connection(client).waiting):false);
      if(completed)failures.delete(id);else failures.set(id,{...value,uncertain:false,message:client?.reason||'状态已刷新，可重试上次操作。'});
    }
  }
  function sessionNotice(session){return session?.state==='locked'?'电脑已锁定，后台服务继续运行；需要桌面交互的操作请解锁后重试。':session&&session.interactive!==true?'电脑交互桌面暂不可用，后台服务继续运行；恢复桌面后可重试。':'';}
  async function chooseInitialize(client){
    const mode=client.id==='zcode'&&client.pipeSupported===true?'private-pipe':'';
    if(client.id==='zcode'&&!mode)return false;
    let accepted=await initializeDialog(client,mode);
    if(!accepted)return false;
    if(mode&&client.running&&client.confirmTaskState){
      accepted=await confirmQuit(client,false,true);
      if(!accepted)return false;
    }
    return mode==='private-pipe'?{connectionMode:mode,acknowledgeUnavailable:accepted==='recovery'}:accepted;
  }
  function initializationFields(choice){
    if(!choice)return {};
    return {initializeDesktop:true,...(choice.connectionMode==='private-pipe'?{connectionMode:'private-pipe'}:{}),
      ...(choice==='recovery'||choice.acknowledgeUnavailable===true?{acknowledgeUnavailable:true}:{})};
  }
  function initializeDialog(client,mode){
    return new Promise(resolve=>{
      const dialog=document.createElement('dialog');dialog.className='client-disable-dialog';
      const node=(tag,text)=>{const element=document.createElement(tag);element.dataset.i18n=text;element.textContent=BridgeI18n.t(text);return element;};
      const title=node('h2','初始化连接');
      const description=node('p',mode==='private-pipe'?'网关将通过本机专用管道连接 ZCode。你可以在电脑锁屏时继续原会话。'
        :client.initializationMode==='background'?'Windows 上优先后台初始化，无需保持键盘焦点；开发者工具可能短暂出现。后台方式不可用时，仅在桌面可交互时尝试前台引导。'
        :'将在电脑端打开 Claude 和开发者工具，并使用键盘焦点完成连接；如遇屏保或锁屏，请恢复桌面后继续。');
      const identity=document.createElement('p');identity.textContent=client.name||client.id;
      const id='client-initialize-'+(++sequence);title.id=id+'-title';description.id=id+'-description';dialog.setAttribute('aria-labelledby',title.id);dialog.setAttribute('aria-describedby',description.id);
      const actions=document.createElement('div');actions.className='client-disable-options';let choice=false;
      const finish=value=>{choice=value;dialog.close();};
      const start=node('button','开始初始化'),cancel=node('button','取消');start.type=cancel.type='button';start.onclick=()=>finish(true);cancel.onclick=()=>finish(false);actions.append(start);
      actions.append(cancel);dialog.append(title,identity,description);
      if(mode&&client.running)dialog.append(node('p','如果当前 ZCode 无法接入，网关将尝试正常退出并重新启动。检测到活动任务时会停止重启。'));
      dialog.append(actions);
      dialog.addEventListener('cancel',event=>{event.preventDefault();finish(false);});dialog.addEventListener('close',()=>{dialog.remove();resolve(choice);},{once:true});document.body.append(dialog);dialog.showModal();cancel.focus();
    });
  }
  function mountConnectionModes(root){
    const field=document.createElement('fieldset'),title=document.createElement('legend'),options=document.createElement('div'),note=document.createElement('p');
    field.className='client-connection-methods';title.textContent=BridgeI18n.t('连接方式');options.className='client-connection-options';note.className='account-muted';note.setAttribute('role','status');field.append(title,options,note);root.append(field);
    const buttons=new Map();let current,onSelect,order='';
    return {element:field,update(client,{disabled=false,select}={}){
      current=client;onSelect=select;const modes=connectionModes(client),next=modes.map(mode=>mode.id).join('|');field.hidden=modes.length<2;title.textContent=BridgeI18n.t('连接方式');
      for(const mode of modes){
        let button=buttons.get(mode.id);if(!button){button=document.createElement('button');button.type='button';button.dataset.connectionMode=mode.id;button.onclick=()=>{if(!button.disabled)onSelect?.(mode.id);};buttons.set(mode.id,button);}
        const selected=mode.id===current.connectionMode;button.textContent=BridgeI18n.t(mode.label);button.setAttribute('aria-pressed',String(selected));button.disabled=disabled||mode.available===false||mode.configured!==true||selected&&connection(current).ready;button.title=BridgeI18n.t(mode.reason||mode.description||'');
      }
      if(order!==next){options.replaceChildren(...modes.map(mode=>buttons.get(mode.id)));order=next;}
      const selected=modes.find(mode=>mode.id===client?.connectionMode),messages=[selected?.description,...modes.filter(mode=>mode.available===false||mode.configured!==true).map(mode=>mode.reason||'请先在电脑网关中配置此连接方式。')].filter(Boolean);
      note.textContent=[...new Set(messages)].map(value=>BridgeI18n.t(value)).join(' ');note.hidden=!note.textContent;
    }};
  }
  function confirmConnectionMode(client,mode){
    const choice=connectionModes(client).find(value=>value.id===mode&&value.available!==false&&value.configured===true);
    if(!choice)return Promise.resolve(false);if(mode===client.connectionMode)return Promise.resolve(true);
    return new Promise(resolve=>{
      const node=(tag,text)=>{const value=document.createElement(tag);value.textContent=BridgeI18n.t(text);return value;},dialog=document.createElement('dialog');dialog.className='client-disable-dialog';
      const title=node('h2','切换应用接入方式？'),identity=node('p',client.name||client.id),target=node('p','切换为');target.textContent+='：'+BridgeI18n.t(choice.label);
      const description=node('p','这将切换此应用的接入方式，不会退出或重启电脑 App。未完成的操作需先处理。');
      const id='client-mode-confirm-'+(++sequence);title.id=id;description.id=id+'-description';dialog.setAttribute('aria-labelledby',title.id);dialog.setAttribute('aria-describedby',description.id);
      const actions=document.createElement('div');actions.className='client-disable-options';let accepted=false;const finish=value=>{accepted=value;dialog.close();};
      const confirm=node('button','确认切换'),cancel=node('button','取消');confirm.type=cancel.type='button';confirm.onclick=()=>finish(true);cancel.onclick=()=>finish(false);actions.append(confirm,cancel);dialog.append(title,identity,target,description);if(choice.description)dialog.append(node('p',choice.description));dialog.append(actions);
      dialog.addEventListener('cancel',event=>{event.preventDefault();finish(false);});dialog.addEventListener('close',()=>{dialog.remove();resolve(accepted);},{once:true});document.body.append(dialog);dialog.showModal();cancel.focus();
    });
  }
  async function chooseDisable(client){
    const choice=await new Promise(resolve=>{
      const dialog=document.createElement('dialog');dialog.className='client-disable-dialog';
      const node=(tag,text)=>{const element=document.createElement(tag);element.dataset.i18n=text;element.textContent=BridgeI18n.t(text);return element;};
      const title=node('h2','停用手机接入'),description=node('p','选择是否同时退出电脑上的应用。');
      const identity=document.createElement('p');identity.textContent=client.name||client.id;
      const id='client-disable-'+(++sequence);title.id=id+'-title';description.id=id+'-description';
      dialog.setAttribute('aria-labelledby',title.id);dialog.setAttribute('aria-describedby',description.id);
      const actions=document.createElement('div');actions.className='client-disable-options';let choice=null;
      const finish=value=>{choice=value;dialog.close();};
      const options=[
        ['仅停用手机接入','保留电脑 App 和现有任务。',false],
        ['正常退出电脑 App',client.confirmTaskState?'通过原生方式退出；无法核验任务时需要再次确认。':client.id==='claude'?'通过 Claude 原生菜单正常退出；如有任务或保存确认，请在电脑端处理。下次开启时会尝试重新连接。':client.id==='codex'&&client.canForceQuit===true?'仅在所有任务结束且没有待确认操作时，通过 Codex 原生菜单正常退出。锁屏时可能不可用；如有退出确认，请在电脑端处理。':'仅在所有任务结束且没有待确认操作时退出。',true]
      ];
      if(client.bindingMode==='manual')options.splice(1);
      if(client.canForceQuit===true)options.push(['强制关闭 App','结束应用进程，可能丢失未保存内容或中断任务。锁屏时也可使用。','force']);
      for(const [label,note,value] of options){
        const button=document.createElement('button');button.type='button';button.dataset.quitDesktop=String(value!==false);
        if(value==='force')button.dataset.forceDesktop='true';
        button.append(node('strong',label),node('small',note));button.onclick=()=>finish(value);actions.append(button);
      }
      const cancel=node('button','取消');cancel.type='button';cancel.onclick=()=>finish(null);actions.append(cancel);
      dialog.append(title,identity,description,actions);
      dialog.addEventListener('cancel',event=>{event.preventDefault();finish(null);});
      dialog.addEventListener('close',()=>{dialog.remove();resolve(choice);},{once:true});
      document.body.append(dialog);dialog.showModal();cancel.focus();
    });
    return choice==='force'?chooseForce(client):choice===true&&(client.confirmTaskState||client.id==='claude'||client.id==='codex'&&client.desktopRestartRecommended===true)?confirmQuit(client,false):choice;
  }
  function chooseForce(client){
    if(client.canForceQuit!==true)return Promise.resolve(null);
    return confirmQuit(client,true);
  }
  function confirmQuit(client,force,restart=false){
    return new Promise(resolve=>{
      const dialog=document.createElement('dialog');dialog.className='client-disable-dialog';
      const node=(tag,text)=>{const element=document.createElement(tag);element.dataset.i18n=text;element.textContent=BridgeI18n.t(text);return element;};
      const title=node('h2',restart?'重新启动 ZCode？':force?'强制关闭确认':client.confirmTaskState?'正常退出确认':client.id==='codex'?'Codex 恢复退出确认':'退出 Claude Desktop 确认'),description=node('p',restart?'当前无法核验所有任务。正常重启可能中断未确认的任务或审批。是否继续？':!force&&client.confirmTaskState?'当前无法核验所有任务。正常退出可能中断正在运行的任务或等待中的审批，是否继续？':!force&&client.id==='codex'?'连接重建失败，可能无法核验全部任务。正常退出仍可能中断无法确认的任务；已知运行任务会阻止退出。请确认接受此风险，退出后再手动开启 Codex。':client.id==='claude'
        ?force?'这会立即结束 Claude Desktop，可能中断正在运行的会话并丢失未保存内容。重新启动后，初始化连接需要电脑处于解锁状态。是否确认强制退出？':'重新启动 Claude Desktop 后，初始化连接需要电脑处于解锁状态。是否确认退出？'
        :'这会立即结束电脑上的应用，可能中断正在运行的会话并丢失未保存内容。请确认仍要继续。');
      const identity=document.createElement('p');identity.textContent=client.name||client.id;
      const id='client-quit-confirm-'+(++sequence);title.id=id+'-title';description.id=id+'-description';dialog.setAttribute('aria-labelledby',title.id);dialog.setAttribute('aria-describedby',description.id);
      const actions=document.createElement('div');actions.className='client-disable-options';let choice=null;
      const finish=value=>{choice=value;dialog.close();};
      const confirm=node('button',restart?'确认重启':force?'确认强制关闭':'确认退出'),cancel=node('button','取消');confirm.type=cancel.type='button';if(force)confirm.className='client-force-confirm';confirm.onclick=()=>finish(force?'force':client.confirmTaskState||client.id==='codex'?'recovery':true);cancel.onclick=()=>finish(null);actions.append(confirm,cancel);dialog.append(title,identity,description,actions);
      dialog.addEventListener('cancel',event=>{event.preventDefault();finish(null);});dialog.addEventListener('close',()=>{dialog.remove();resolve(choice);},{once:true});document.body.append(dialog);dialog.showModal();cancel.focus();
    });
  }
  return {chooseDisable,chooseForce,chooseInitialize,mountConnectionModes,confirmConnectionMode,connectionModes,connectionModeLabel,initializationFields,connection,request,failure,failureMessage,retryLabel,reconcile,sessionNotice};
})();
if(typeof module!=='undefined')module.exports={...ClientLifecycle,ClientIdentity};
