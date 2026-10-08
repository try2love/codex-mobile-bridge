'use strict';
const qrEntries=new Map();
let qrPolling=false;
function pairingLabel(tag,text,cls=''){
  const node=document.createElement(tag);node.textContent=text;if(cls)node.className=cls;return node;
}
function appendPairing(card,url,running){
  let entry=qrEntries.get(url);
  if(!entry){
    const node=document.createElement('details');node.className='qr-entry';
    const summary=pairingLabel('summary',''),body=document.createElement('div'),image=document.createElement('img');
    image.width=image.height=320;image.hidden=true;
    const status=pairingLabel('p','','qr-status'),error=pairingLabel('p','','error'),hint=pairingLabel('p','','hint'),privacy=pairingLabel('p','','hint'),refresh=pairingLabel('button','','primary');
    status.setAttribute('role','status');error.setAttribute('role','alert');refresh.type='button';
    body.className='qr-body';body.append(image,status,error,hint,privacy,refresh);node.append(summary,body);
    entry={url,node,summary,image,status,error,hint,privacy,refresh,grant:null,generation:0,busy:false,running};qrEntries.set(url,entry);
    summary.onclick=event=>{if(!entry.running)event.preventDefault();};
    node.ontoggle=()=>{if(node.open&&entry.running)refreshPairing(entry);else clearPairing(entry);};
    refresh.onclick=()=>refreshPairing(entry);
  }
  entry.running=running;
  if(!running&&entry.node.open)entry.node.open=false;
  entry.summary.setAttribute('aria-disabled',String(!running));card.append(entry.node);renderPairingEntry(entry);
}
function renderPairingEntry(entry){
  const {grant}=entry;
  const remaining=grant?Math.max(0,Math.ceil(grant.expires-Date.now()/1000)):0;
  const state=grant?.state==='active'&&!remaining?'expired':grant?.state;
  const active=entry.node.open&&state==='active';
  entry.summary.textContent=t('扫码登录')+(entry.running?'':t(' · 网关未启动'));
  entry.image.alt=t('扫码登录');entry.image.hidden=!active;
  if(active&&entry.image.src!==grant.image)entry.image.src=grant.image;
  if(!active)entry.image.removeAttribute('src');
  entry.status.textContent=entry.busy?t('正在检查连接并生成二维码…'):state==='used'?t('手机已登录，此二维码已失效。'):state==='expired'?t('二维码已过期，请刷新。'):active?t('剩余有效时间：')+Math.floor(remaining/60)+':'+String(remaining%60).padStart(2,'0'):'';
  entry.error.textContent=t(entry.errorMessage||'');entry.error.hidden=!entry.errorMessage;
  entry.hint.textContent=t('使用手机 App 的“扫码连接电脑”扫描后即可登录，无需输入网关密码。也可以使用手机相机在浏览器中打开。手机需要能访问此地址。');
  entry.privacy.textContent=t('二维码 5 分钟内有效，仅可使用一次。持有码即可登录，请勿分享截图；收起或刷新会撤销旧码。手机 App 绑定后可持续使用，直到退出登录或在“登录设备”中撤销；浏览器遵循登录有效期设置。');
  entry.refresh.textContent=t('刷新二维码');entry.refresh.disabled=entry.busy||!entry.running;
}
function renderPairing(){for(const entry of qrEntries.values())renderPairingEntry(entry);}
function refreshPairing(entry){
  const generation=++entry.generation;
  const preceding=entry.grant;entry.busy=true;entry.grant=null;entry.errorMessage='';renderPairingEntry(entry);
  // Rapid collapse/reopen must not let an older probe revoke a newer code.
  const previous=entry.task||Promise.resolve();
  entry.task=(async()=>{
    await previous;
    if(generation!==entry.generation)return;
    try{
      if(preceding)await api.pairing({action:'revoke',id:preceding.id});
      const grant=await api.pairing({action:'create',url:entry.url});
      if(generation!==entry.generation){await api.pairing({action:'revoke',id:grant.id});return;}
      entry.grant=grant;
    }catch(error){if(generation===entry.generation)entry.errorMessage=error.message.replace(/^Error invoking remote method '[^']+': (?:Error: )?/,'');}
    finally{if(generation===entry.generation){entry.busy=false;renderPairingEntry(entry);}}
  })();
  return entry.task;
}
function clearPairing(entry){
  const grant=entry.grant;entry.generation++;entry.grant=null;entry.busy=false;
  entry.image.removeAttribute('src');entry.image.hidden=true;
  if(grant)api.pairing({action:'revoke',id:grant.id}).catch(()=>{});
}
function resetPairing(){for(const entry of qrEntries.values()){clearPairing(entry);entry.node.open=false;}qrEntries.clear();}
setInterval(renderPairing,1000);
setInterval(()=>{
  const entries=[...qrEntries.values()].filter(e=>e.node.open&&e.grant?.state==='active'&&e.grant.expires>Date.now()/1000);
  if(qrPolling||!entries.length)return;
  const grants=entries.map(e=>e.grant);qrPolling=true;
  api.pairing({action:'status',ids:grants.map(g=>g.id)}).then(value=>{
    entries.forEach((entry,i)=>{if(entry.grant===grants[i])entry.grant.state=value.states[grants[i].id]||'expired';});
  }).catch(()=>entries.forEach((entry,i)=>{if(entry.grant===grants[i])entry.grant.state='expired';})).finally(()=>{qrPolling=false;renderPairing();});
},3000);
