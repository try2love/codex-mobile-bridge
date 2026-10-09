'use strict';
const ClientLifecycle=(()=>{
  let sequence=0;
  function connection(client={}){
    const stage=client.connectionState,setup=client.setupStatus;
    const waiting=['starting','connecting'].includes(stage)||(!stage&&['starting','connecting'].includes(setup));
    const background=client.backgroundRunning&&client.mainRunning===false;
    let label,reason=client.reason||'',pending=false,action=false;
    if(setup==='recovery-required'||(background&&!waiting&&!client.pendingEnable))label=background?'后台运行，桌面未打开':'需要恢复连接';
    else if(client.connected)label='已连接';
    else if(client.pendingEnable){label='正在启动应用…';reason='正在等待客户端连接，完成后会自动显示聊天。';pending=true;}
    else if(waiting){label=stage==='starting'?'正在启动应用…':'正在连接，请稍候…';pending=true;}
    else{
      label=({'needs-unlock':'电脑已锁定','needs-desktop':'电脑桌面暂不可用'})[setup]||({'error':'连接失败','timeout':'连接超时','needs-initialization':'需要初始化连接'})[stage]
        ||({'unsupported':'暂不支持自动接入','needs-initialization':'需要初始化连接','needs-first-launch':'需要完成首次设置','restart-required':'等待重启','needs-permission':'等待授权','needs-developer-mode':'等待开发者模式确认','needs-trust':'等待目录授权','cancelled':'已取消连接','failed':'连接失败','needs-retry':'连接失败'})[setup];
      action=!!label;
      label=label||(client.installed===false?'未安装':client.running===true?'应用运行中，尚未连接':client.running===false?'应用未运行':'尚未连接');
    }
    const canInitialize=client.id==='claude'&&client.enabled&&client.installed!==false&&!client.connected&&!pending&&(['needs-initialization','needs-retry','needs-developer-mode','needs-trust','needs-permission','needs-unlock','needs-desktop','cancelled','failed'].includes(setup)||stage==='needs-initialization');
    const retryable=client.retryable===true&&client.enabled&&!client.connected&&!pending&&!canInitialize&&!['recovery-required','restart-required'].includes(setup);
    return {label,reason,waiting:pending,action:action||setup==='recovery-required',retryable:!!retryable,canInitialize:!!canInitialize,error:!client.connected&&['error','timeout'].includes(stage)||!client.connected&&setup==='failed'};
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
      const completed=client&&(['quit','force'].includes(value.action)?client.enabled===false&&client.running===false:value.action==='disable'?client.enabled===false:value.action==='initialize'?client.connected||connection(client).waiting:value.action==='enable'?client.enabled&&(client.running||client.connected||connection(client).waiting):false);
      if(completed)failures.delete(id);else failures.set(id,{...value,uncertain:false,message:client?.reason||'状态已刷新，可重试上次操作。'});
    }
  }
  function sessionNotice(session){return session?.state==='locked'?'电脑已锁定，后台服务继续运行；需要桌面交互的操作请解锁后重试。':session&&session.interactive!==true?'电脑交互桌面暂不可用，后台服务继续运行；恢复桌面后可重试。':'';}
  function chooseInitialize(client){
    return new Promise(resolve=>{
      const dialog=document.createElement('dialog');dialog.className='client-disable-dialog';
      const node=(tag,text)=>{const element=document.createElement(tag);element.dataset.i18n=text;element.textContent=BridgeI18n.t(text);return element;};
      const title=node('h2','初始化连接'),description=node('p','Windows 上优先后台初始化，无需保持键盘焦点；开发者工具可能短暂出现。后台方式不可用时，仅在桌面可交互时尝试前台引导。');
      const identity=document.createElement('p');identity.textContent=client.name||client.id;
      const id='client-initialize-'+(++sequence);title.id=id+'-title';description.id=id+'-description';dialog.setAttribute('aria-labelledby',title.id);dialog.setAttribute('aria-describedby',description.id);
      const actions=document.createElement('div');actions.className='client-disable-options';let choice=false;
      const finish=value=>{choice=value;dialog.close();};
      const start=node('button','开始初始化'),cancel=node('button','取消');start.type=cancel.type='button';start.onclick=()=>finish(true);cancel.onclick=()=>finish(false);actions.append(start,cancel);dialog.append(title,identity,description,actions);
      dialog.addEventListener('cancel',event=>{event.preventDefault();finish(false);});dialog.addEventListener('close',()=>{dialog.remove();resolve(choice);},{once:true});document.body.append(dialog);dialog.showModal();cancel.focus();
    });
  }
  function chooseDisable(client){
    return new Promise(resolve=>{
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
        ['同时退出电脑 App',client.id==='claude'?'通过 Claude 原生菜单正常退出；如有任务或保存确认，请在电脑端处理。下次开启时会尝试重新连接。':'仅在所有任务结束且没有待确认操作时退出。',true]
      ];
      if(client.canForceQuit===true)options.push(['后台强制结束','结束应用进程，可能丢失未保存内容或中断任务。锁屏时也可使用。','force']);
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
  }
  return {chooseDisable,chooseInitialize,connection,request,failure,failureMessage,retryLabel,reconcile,sessionNotice};
})();
if(typeof module!=='undefined')module.exports=ClientLifecycle;
