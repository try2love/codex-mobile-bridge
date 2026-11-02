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
    return {passed:7,width:innerWidth};
  }finally{panel.close();}
}
