// Production remote UI with synthetic client state. Never use on a real gateway.
async function runCodexInitializationChecks(){
  const checks=[],check=(value,label)=>{if(!value)throw Error(label);checks.push(label);};
  const nav=clientNavigation,originalRequest=nav.request,originalClients=nav.clients;
  const calls=[],native={id:'codex',name:'Codex',installed:true,configured:true,enabled:true,running:true,
    connected:false,canInitializeDesktop:true,setupStatus:'needs-initialization'};
  let clients=[native];
  nav.request=async(path,body)=>{
    if(path==='/api/clients'&&body?.initializeDesktop===true){
      calls.push({path,body});clients=[{...native,canInitializeDesktop:false,connectionState:'connecting'}];
      return {switch:{phase:'preparing',operation:'desktopRuntime'}};
    }
    if(path==='/api/clients')return {clients};
    throw Error('Unexpected fixture request: '+path);
  };
  try{
    nav.applyClients({clients});nav.openManager();
    const row=()=>nav.manager.list.querySelector('[data-client-id="codex"]');
    const initialize=()=>[...row().querySelectorAll('button')].find(button=>button.textContent==='初始化连接');
    check(!!initialize(),'disconnected App offers initialization in remote management');
    let pending=nav.initialize('codex');
    let dialog=document.querySelector('dialog.client-disable-dialog[open]');
    check(!!dialog&&dialog.textContent.includes('正常退出并重新启动 Codex'),'confirmation explains one automatic restart');
    check(!dialog.textContent.includes('开发者工具'),'Codex confirmation uses its own initialization flow');
    [...dialog.querySelectorAll('button')].find(button=>button.textContent==='取消').click();await pending;
    check(calls.length===0,'cancel makes no initialization request');
    pending=nav.initialize('codex');dialog=document.querySelector('dialog.client-disable-dialog[open]');
    [...dialog.querySelectorAll('button')].find(button=>button.textContent==='开始初始化').click();await pending;
    check(calls.length===1&&calls[0].path==='/api/clients'&&calls[0].body.provider==='codex','accept uses exactly one relay-compatible native transaction');
    check(calls[0].body.confirmed&&calls[0].body.tasksConfirmed&&/^[0-9a-f-]{36}$/.test(calls[0].body.requestId),'request carries explicit confirmation and a unique operation ID');
    check(!initialize(),'pending preparation does not offer a duplicate initialization');
    clients=[{...native,connected:true,setupStatus:'ready'}];nav.applyClients({clients});
    check(row().textContent.includes('重新初始化连接')&&row().textContent.includes('已连接'),'reported connection retains explicit manual recovery');
    return {width:innerWidth,passed:checks.length,checks};
  }finally{
    nav.manager?.dialog.close();nav.request=originalRequest;nav.applyClients({clients:originalClients});
  }
}

async function runCodexGatewayInitializationChecks(){
  const checks=[],calls=[],check=(value,label)=>{if(!value)throw Error(label);checks.push(label);};
  const root=document.createElement('div');root.id='client-overview';document.body.append(root);
  const panel=Object.assign(Object.create(DesktopConnectionsPanel.prototype),{
    clients:[{id:'codex',name:'Codex',installed:true,configured:true,enabled:true,running:true,
      connected:false,canInitializeDesktop:true,setupStatus:'needs-initialization'}],
    root,gatewayRunning:true,clientsRevision:0,statusRevision:0,accountPanels:{},operationFailures:new Map(),
    api:{accounts:async body=>{calls.push(body);return {switch:{phase:'preparing'}};}},feedback(){},
    renderDetails(){},renderAccount(){},resumeAccounts(){},syncPolling(){},renderScan(){},refreshClients:async()=>{}
  });
  try{
    panel.renderClients();
    check([...root.querySelectorAll('button')].some(button=>button.textContent==='初始化连接'),'gateway management exposes initialization');
    const pending=panel.toggleClient('codex',true,{initializeDesktop:true});
    const dialog=document.querySelector('dialog.client-disable-dialog[open]');
    check(!!dialog,'gateway uses the same native initialization confirmation');
    [...dialog.querySelectorAll('button')].find(button=>button.textContent==='开始初始化').click();await pending;
    check(calls.length===1&&calls[0].action==='initializeDesktopRuntime','gateway calls the same account transaction once');
    panel.clients[0]={...panel.clients[0],connected:true};panel.renderClients();
    check([...root.querySelectorAll('button')].some(button=>button.textContent==='重新初始化连接'),'gateway retains explicit manual recovery for reported connections');
    return {width:innerWidth,passed:checks.length,checks};
  }finally{root.remove();}
}
