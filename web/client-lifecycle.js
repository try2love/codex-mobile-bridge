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
      label=({'error':'连接失败','timeout':'连接超时','needs-initialization':'需要初始化连接'})[stage]
        ||({'unsupported':'暂不支持自动接入','needs-initialization':'需要初始化连接','needs-first-launch':'需要完成首次设置','restart-required':'等待重启','needs-permission':'等待授权','needs-developer-mode':'等待开发者模式确认','needs-trust':'等待目录授权','cancelled':'已取消连接','failed':'连接失败','needs-retry':'连接失败'})[setup];
      action=!!label;
      label=label||(client.installed===false?'未安装':client.running===true?'应用运行中，尚未连接':client.running===false?'应用未运行':'尚未连接');
    }
    const canInitialize=client.id==='claude'&&client.enabled&&client.installed!==false&&!client.connected&&!pending&&(['needs-initialization','needs-retry','needs-developer-mode','needs-trust','needs-permission','cancelled'].includes(setup)||stage==='needs-initialization');
    const retryable=client.retryable===true&&client.enabled&&!client.connected&&!pending&&!canInitialize&&!['recovery-required','restart-required'].includes(setup);
    return {label,reason,waiting:pending,action:action||setup==='recovery-required',retryable:!!retryable,canInitialize:!!canInitialize,error:!client.connected&&['error','timeout'].includes(stage)||!client.connected&&setup==='failed'};
  }
  function chooseInitialize(client){
    return new Promise(resolve=>{
      const dialog=document.createElement('dialog');dialog.className='client-disable-dialog';
      const node=(tag,text)=>{const element=document.createElement(tag);element.dataset.i18n=text;element.textContent=BridgeI18n.t(text);return element;};
      const title=node('h2','初始化连接'),description=node('p','会打开电脑上的 Claude 和开发者工具，并使用键盘焦点；请暂停电脑端操作，完成后恢复后台连接。');
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
      for(const [label,note,value] of [
        ['仅停用手机接入','保留电脑 App 和现有任务。',false],
        ['同时退出电脑 App',client.id==='claude'?'通过 Claude 原生菜单退出，菜单可能短暂出现；如有任务或保存确认，请在电脑端处理。完全退出后需重新初始化连接，初始化会使用电脑前台和键盘焦点。':'仅在所有任务结束且没有待确认操作时退出。',true]
      ]){
        const button=document.createElement('button');button.type='button';button.dataset.quitDesktop=String(value);
        button.append(node('strong',label),node('small',note));button.onclick=()=>finish(value);actions.append(button);
      }
      const cancel=node('button','取消');cancel.type='button';cancel.onclick=()=>finish(null);actions.append(cancel);
      dialog.append(title,identity,description,actions);
      dialog.addEventListener('cancel',event=>{event.preventDefault();finish(null);});
      dialog.addEventListener('close',()=>{dialog.remove();resolve(choice);},{once:true});
      document.body.append(dialog);dialog.showModal();cancel.focus();
    });
  }
  return {chooseDisable,chooseInitialize,connection};
})();
if(typeof module!=='undefined')module.exports=ClientLifecycle;
