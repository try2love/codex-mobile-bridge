async function runCliReleaseChecks(){
  const check=(value,message)=>{if(!value)throw Error(message);};
  const calls=[],notices=[];
  let scope='gateway-a',operations=[],connections=[
    {key:'a',provider:'codex',sessionId:'original',host:'local',location:'本机 CLI',title:'Local'},
    {key:'b',provider:'codex',sessionId:'wsl-session',host:'wsl:ubuntu',location:'WSL · Ubuntu',title:'WSL'},
    {key:'c',provider:'claude',sessionId:'ssh-session',host:'ssh:fixture',location:'SSH fixture',title:'SSH'}];
  const request=async(url,body)=>{check(url==='/api/cli-connections','one shared endpoint');if(body){calls.push(body);operations=[{...body,results:connections.filter(r=>body.connections.includes(r.key)).map(r=>({...r,status:'released'}))}];connections=connections.filter(r=>!body.connections.includes(r.key));return operations[0];}return {connections,operations};};
  const panel=new CliConnectionsPanel({request,notify:v=>notices.push(v),identity:()=>scope});
  const settle=async()=>{for(let i=0;i<5;i++)await Promise.resolve();};
  const button=text=>[...panel.dialog.querySelectorAll('button')].find(b=>b.textContent===text);
  try{
    panel.open({provider:'codex',sessionId:'original',host:'local'});await settle();
    check(panel.dialog.querySelectorAll('input').length===1,'single menu filters original runtime');
    button('释放连接').click();await settle();check(calls.length===1&&calls[0].mode==='wait'&&calls[0].connections.join()==='a','single click releases original only');
    check(panel.dialog.textContent.includes('连接已释放'),'result is visible');
    panel.open();await settle();check(panel.dialog.textContent.includes('WSL')&&panel.dialog.textContent.includes('SSH'),'global entry includes all CLI runtimes');
    button('选择全部').click();button('停止并释放…').click();check(calls.length===1,'stop requires explicit confirmation');button('取消').click();
    button('释放全部').click();await settle();check(calls.length===2&&calls[1].mode==='wait'&&calls[1].connections.join()==='b,c','release all uses current gateway snapshot');
    check(panel.dialog.textContent.includes('没有由网关持有的 CLI 连接'),'released connections leave the list');
    const menu=new SessionMenu();let opened=0;menu.show({title:'CLI',sessionId:'s',pin:()=>{},releaseCli:()=>opened++});
    [...menu.box.querySelectorAll('button')].find(b=>b.textContent.includes('释放 CLI 连接')).click();check(opened===1,'shared title/long-press menu invokes same release callback');menu.close({immediate:true});
    panel.open();await settle();scope='gateway-b';await panel.submit('stop');check(calls.length===2,'changed gateway cannot receive stale operation');
    panel.close();
    const old={key:'old-generation',provider:'codex',sessionId:'s',host:'local',title:'Original',canUnload:true};
    let pending,rows=[old],jobs=[{id:'old',mode:'wait',results:[{...old,status:'waiting'}]}],writes=[];
    const recovery=new CliConnectionsPanel({request:async(_url,body)=>{
      if(!body)return {connections:rows,operations:jobs};
      writes.push(body);await new Promise(resolve=>pending=resolve);return body;
    },notify:()=>{}});
    try{
      recovery.open();await settle();
      const find=text=>[...recovery.dialog.querySelectorAll('button')].find(button=>button.textContent===text);
      check(!recovery.dialog.querySelector('input').disabled,'waiting release remains selectable for stop upgrade');
      find('选择全部').click();find('停止并释放…').click();find('确认停止并释放').click();await settle();
      check(writes.length===1&&writes[0].mode==='stop','confirmed stop upgrades waiting release');
      check(!find('关闭').disabled,'close stays available while native request is pending');
      find('关闭').click();check(!recovery.dialog,'pending release does not trap the page');pending();await settle();
      rows=[];jobs=[{id:'unknown',mode:'wait',results:[{...old,status:'unknown'}]}];recovery.open();await settle();
      find('核对结果').click();await settle();
      check(writes.at(-1).mode==='check'&&writes.at(-1).connections[0]==='old-generation','unknown result checks original generation without a listed connection');
      pending();await settle();
      jobs=[{id:'detached',results:[{...old,status:'detached',unsubscribed:true}]}];await recovery.refresh();
      check(!!find('核对结果'),'confirmed detach offers read-only unload verification');
      const beforeUnload=writes.length;find('释放原生占用…').click();
      check(writes.length===beforeUnload&&recovery.dialog.textContent.includes('其他客户端不再发起任务'),'shared native release requires a separate scoped confirmation');
      find('确认释放原生占用').click();await settle();
      check(writes.at(-1).mode==='unload'&&writes.at(-1).connections[0]==='old-generation','explicit maintenance is separate from read-only verification');
      pending();await settle();
      jobs=[{id:'failed',results:[{...old,status:'failed',notSent:true}]},{id:'old',results:[{...old,status:'superseded'}]}];await recovery.refresh();
      check(recovery.dialog.textContent.includes('尚未释放，可重试')&&recovery.dialog.textContent.includes('已改为停止并释放'),'retryable and superseded receipts remain explicit');
    }finally{recovery.close();pending?.();}
    let finishRead;
    const slowRead=new CliConnectionsPanel({request:()=>new Promise(resolve=>finishRead=resolve),notify:()=>{}});
    slowRead.open();await settle();
    const dismiss=slowRead.dialog.querySelector('[data-cli-close]');
    check(!!dismiss&&!dismiss.disabled,'initial connection read never hides the close action');
    dismiss.click();finishRead({connections:[],operations:[]});await settle();
    check(!slowRead.dialog,'late GET cannot reopen a dismissed panel');
    let complete,failed;
    menu.show({title:'CLI',cliNetwork:()=>new Promise((resolve,reject)=>{complete=resolve;failed=reject;})});
    await Promise.all(menu.box.getAnimations().map(animation=>animation.finished.catch(()=>{})));
    [...menu.box.querySelectorAll('button')].find(button=>button.textContent.includes('检查 CLI 网络')).click();await settle();
    check(menu.box.textContent.includes('正在检查 CLI 网络'),'slow diagnostics show progress inside the current modal');
    failed(Error('Synthetic network unavailable'));await settle();
    check(menu.box.querySelector('.sm-error')?.textContent==='Synthetic network unavailable','diagnostic failures remain visible inside modal');
    toast('Synthetic modal notice');
    const notice=menu.box.querySelector('.toast');check(!!notice,'shared notices enter the active modal top layer');
    const rect=notice.getBoundingClientRect(),top=document.elementFromPoint(rect.left+rect.width/2,rect.top+rect.height/2);
    check(notice===top||notice.contains(top),'modal notice is actually visible and hit-testable');
    menu.close({immediate:true});toast('Synthetic page notice');check(!document.getElementById('toast').hidden,'shared notice works again after modal closes');
    return {passed:23,width:innerWidth};
  }finally{panel.close();}
}
