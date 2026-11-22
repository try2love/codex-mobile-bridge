'use strict';
(() => {
  const cache=()=>window.BridgeChatSyncCache;
  const native=()=>window.CodexBridgeNativeOffline;
  let generation=0,allowed=false,focusing='',focusToken=null,running=false,seen=new Set(),pending=[],firstLists=new Map();
  const enabled=()=>!!native()&&cache()?.supported()&&allowed;
  function reset(){
    generation++;allowed=false;focusing='';focusToken=null;running=false;seen=new Set();pending=[];firstLists=new Map();
  }
  function queue(){
    if(!enabled())return;
    for(const list of firstLists.values())for(const target of list){
      if(!seen.has(target.scope)){seen.add(target.scope);pending.push({target,attempt:0});}
    }
    void drain();
  }
  async function drain(){
    if(running||focusing||!enabled()||!pending.length)return;
    const token=generation;running=true;
    try{
      if(!window.BridgeChatSync)await window.BridgeFeatures?.load('chat');
      while(token===generation&&enabled()&&!focusing&&pending.length&&window.BridgeChatSync){
        const item=pending.shift();
        try{
          await window.BridgeChatSync.prefetch({...item.target,authorized:()=>token===generation&&cache().active(item.target.scope),
            active:()=>token===generation&&enabled()&&!focusing});
          // A foreground request may have taken priority before this request was sent.
          if(focusing&&token===generation)pending.unshift(item);
        }catch{
          if(token===generation&&item.attempt===0)pending.push({...item,attempt:1});
        }
      }
    }finally{if(token===generation)running=false;}
  }
  async function network(hint){
    if(!native()||!cache()?.owner)return;
    const token=generation;
    let state=hint;
    if(typeof state?.allowPrefetch!=='boolean'){
      try{state=await cache().event('state');}catch{state=null;}
    }
    if(token!==generation)return;
    const next=state?.allowPrefetch===true;
    if(!next){allowed=false;return;}
    if(!allowed){seen.clear();pending=[];}
    allowed=true;queue();
  }
  window.BridgeSyncPrefetch={
    reset,network,
    list(provider,rows,targetFor){
      if(!(native()||typeof BridgeHost!=='undefined'&&BridgeHost.isNativeApp())||!cache()?.supported()||firstLists.has(provider)||!rows?.length)return;
      const list=rows.map(targetFor).filter(item=>item?.scope);
      if(!list.length)return;
      firstLists.set(provider,list);
      if(enabled())queue();else void network();
    },
    focus(scope,token){focusing=scope||'';focusToken=token;if(scope)pending=pending.filter(item=>item.target.scope!==scope);},
    settled(scope,token){if(scope===focusing&&token===focusToken){focusing='';focusToken=null;void drain();}},
  };
  window.addEventListener('codexbridge-native-offline-state',event=>{void network(event.detail);});
  document.addEventListener('visibilitychange',()=>{if(!document.hidden)void network();});
  function label(name){
    return typeof BridgeI18n==='undefined'?name:BridgeI18n.t(name);
  }
  function textNode(tag,content){
    const element=document.createElement(tag);element.textContent=content;return element;
  }
  async function usage(request){
    const dialog=document.getElementById('transfer-usage-dialog'),list=dialog.querySelector('[data-usage]');
    list.replaceChildren(textNode('p',label('正在读取…')));dialog.showModal();
    try{
      const data=await request('/api/session-transfers');if(!dialog.open)return;
      const bytes=value=>window.BridgeHistoryTransfer?.size(Number(value))||Number(value).toLocaleString()+' B';
      list.replaceChildren(textNode('p',label('合计')+' '+bytes(data.totalBytes)));
      if(Number.isFinite(data.totalReusedBytes))list.append(textNode('p',label('缓存复用')+' '+bytes(data.totalReusedBytes)));
      if(!data.sessions.length){list.append(textNode('p',label('暂无会话传输记录。')));return;}
      const viewport=document.createElement('div'),table=document.createElement('table');
      viewport.className='transfer-usage-scroll';viewport.tabIndex=0;
      viewport.setAttribute('role','region');viewport.setAttribute('aria-label',label('传输用量'));
      const header=table.createTHead().insertRow();
      for(const name of ['Agent 平台与连接方式','已同步数据量','聊天名称']){
        const cell=textNode('th',label(name));cell.scope='col';header.append(cell);
      }
      const providers={codex:'Codex',claude:'Claude',deepseek:'DeepSeek',zcode:'ZCode',antigravity:'Antigravity'};
      const methods={app:'APP',cli:'CLI',ide:'IDE',code:'Code',cowork:'Cowork'},body=table.createTBody();
      for(const item of data.sessions){
        const row=body.insertRow(),source=row.insertCell(),amount=row.insertCell(),chat=row.insertCell();
        const family=item.provider.split(':')[0],host=item.hostLabel||item.host;
        source.textContent=[providers[family]||family,methods[item.method],host==='local'?label('此电脑'):host].filter(Boolean).join(' · ');
        source.title=source.textContent;amount.textContent=bytes(item.bytes);
        amount.title=Number(item.bytes).toLocaleString()+' B';chat.textContent=item.title||item.sessionId;chat.title=chat.textContent;
      }
      viewport.append(table);list.append(viewport);
    }catch(error){list.replaceChildren(textNode('p',error.message));}
  }
  async function cleanup(){
    const dialog=document.getElementById('transfer-clear-dialog'),body=dialog.querySelector('[data-clear]'),submit=dialog.querySelector('[data-confirm]');
    const groups=await cache().inventory(),gateways=new Set(),days=new Set();
    let all=false,confirming=false;
    const heading=name=>body.append(textNode('h4',label(name)));
    const checkbox=(name,checked,onchange)=>{
      const input=document.createElement('input'),line=document.createElement('label');
      input.type='checkbox';input.checked=checked;line.className='transfer-choice';
      input.onchange=()=>onchange(input.checked);line.append(input,document.createTextNode(name));body.append(line);
    };
    const draw=()=>{
      body.replaceChildren();submit.hidden=!groups.length;
      if(!groups.length){body.append(textNode('p',label('此浏览器暂无已缓存的回合。')));return;}
      heading('选择网关');
      for(const group of groups)checkbox(group.name,gateways.has(group.id),yes=>{
        if(yes)gateways.add(group.id);else gateways.delete(group.id);
        all=false;days.clear();draw();
      });
      if(gateways.size){
        heading('按提问日期清理');checkbox(label('全部日期'),all,yes=>{all=yes;days.clear();draw();});
        if(!all){
          const available=new Set(groups.filter(g=>gateways.has(g.id)).flatMap(g=>[...g.dates]));
          for(const day of [...available].sort().reverse())checkbox(day,days.has(day),yes=>{if(yes)days.add(day);else days.delete(day);draw();});
        }
      }
      submit.disabled=!gateways.size||!all&&!days.size;submit.textContent=label('下一步');
    };
    submit.onclick=async()=>{
      if(!confirming){
        confirming=true;body.replaceChildren(textNode('p',label('将清除以下网关在此浏览器的缓存。')));
        for(const group of groups.filter(g=>gateways.has(g.id)))body.append(textNode('p',group.name+' · '+(all?label('全部日期'):[...days].sort().join('、'))));
        submit.textContent=label('确认清理');return;
      }
      submit.disabled=true;
      try{await cache().erase(gateways,all?null:days);dialog.close();}catch(error){body.replaceChildren(textNode('p',error.message));submit.disabled=false;}
    };
    draw();dialog.showModal();
  }
  window.BridgeTransferSettings={usage,cleanup};
})();
