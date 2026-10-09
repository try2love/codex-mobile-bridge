'use strict';
(()=>{
  const api=window.bridgeDesktop;
  const say=zh=>BridgeI18n.t(zh);
  const root=document.createElement('article');root.id='shared-relay';
  const node=(tag,text)=>{const value=document.createElement(tag);if(text){value.textContent=text;const source=BridgeI18n.source(text);if(source)value.dataset.i18n=source;}return value;};
  root.append(node('h2',say("共享中继 · 内测")),node('p',say("使用管理员提供的 HTTPS 地址和一次性邀请码连接自己的电脑。电脑需要保持唤醒，Codex 和网关需要保持运行。")));
  const registration=node('div'),controls=node('div'),status=node('p'),phones=node('div'),pair=node('div');
  status.setAttribute('role','status');
  const field=(caption,type,id)=>{const label=node('label',caption),input=node('input');input.type=type;input.id=id;label.append(input);registration.append(label);return input;};
  const url=field(say("中继 HTTPS 地址"),'url','relay-url');url.placeholder='https://relay.example.com';
  const invitation=field(say("一次性邀请码"),'password','relay-invitation');invitation.autocomplete='off';
  const name=field(say("电脑名称"),'text','relay-name');name.maxLength=80;
  const consent=field(say("我信任此中继运营者，并允许已配对手机操作这台电脑。中继可读取经过的对话。"),'checkbox','relay-consent');
  let busy=false,poll=null,expiry=null,phoneSignature='',lastStatus=null;
  function button(text,run,parent){const b=node('button',text);b.type='button';b.onclick=()=>perform(run);parent.append(b);return b;}
  async function perform(run){if(busy)return;busy=true;root.querySelectorAll('button').forEach(b=>b.disabled=true);try{await run();}catch(e){status.textContent=e.message;}finally{busy=false;root.querySelectorAll('button').forEach(b=>b.disabled=false);}}
  async function refresh(){
    render(await api.sharedRelay({action:'status'}));
  }
  function render(value){
    lastStatus=value;
    registration.hidden=!!value.registered;controls.hidden=!value.registered;
    if(!value.registered){status.textContent=say("尚未连接共享中继。");return;}
    status.textContent=value.error||value.deviceName+' · '+(!value.enabled?say("已暂停"):value.online?say("在线"):say("离线；启用后请重新启动电脑网关。"));
    toggle.textContent=value.enabled?say("暂停中继连接"):say("启用中继连接");toggle.dataset.enabled=String(value.enabled);toggle.dataset.i18n=value.enabled?'暂停中继连接':'启用中继连接';
    const signature=JSON.stringify([BridgeI18n.language(),value.pending,value.phones]);
    if(signature===phoneSignature)return;
    phoneSignature=signature;phones.replaceChildren();
    for(const phone of value.pending||[]){const row=node('p',say("等待批准：")+phone.name);button(say("批准"),async()=>{await api.sharedRelay({action:'approve',id:phone.id,approved:true});await refresh();},row);button(say("拒绝"),async()=>{await api.sharedRelay({action:'approve',id:phone.id,approved:false});await refresh();},row);phones.append(row);}
    for(const phone of value.phones||[]){const row=node('p',phone.name);button(say("撤销手机"),async()=>{await api.sharedRelay({action:'revoke-phone',id:phone.id});await refresh();},row);phones.append(row);}
  }
  button(say("注册这台电脑"),async()=>{
    if(!consent.checked)throw Error(say("请先确认授权。"));
    await api.sharedRelay({action:'register',url:url.value.trim(),invitation:invitation.value.trim(),name:name.value.trim(),consent:true});
    invitation.value='';await refresh();status.textContent=say("电脑已注册。请在“连接与状态”重新启动网关，然后生成手机配对。");
  },registration);
  button(say("刷新状态"),refresh,root);
  button(say("生成手机配对"),async()=>{
    const grant=await api.sharedRelay({action:'pair'});pair.replaceChildren();
    const image=node('img');image.src=grant.image;image.alt=say("手机配对二维码");image.width=240;image.height=240;
    const link=node('input');link.readOnly=true;link.value=grant.url;link.setAttribute('data-i18n-aria-label','一次性配对链接');link.setAttribute('aria-label',say("一次性配对链接"));link.onclick=()=>link.select();
    pair.append(image,link,node('p',say("5 分钟内有效。手机申请后，请在此处核对名称并批准。")));
    clearInterval(poll);poll=setInterval(()=>{if(!document.hidden&&!root.closest('[data-panel]').hidden&&!busy)perform(refresh);},5000);
    clearTimeout(expiry);expiry=setTimeout(()=>{clearInterval(poll);pair.replaceChildren();},300000);
  },controls);
  const toggle=button(say("暂停中继连接"),async()=>{await api.sharedRelay({action:toggle.dataset.enabled==='true'?'disable':'enable'});await refresh();},controls);
  button(say("撤销电脑注册"),async()=>{
    if(!confirm(say("撤销后所有已配对手机将失去访问权限。重新接入需要新的邀请码。")))return;
    await api.sharedRelay({action:'revoke'});pair.replaceChildren();phones.replaceChildren();phoneSignature='';clearInterval(poll);clearTimeout(expiry);await refresh();
  },controls);
  root.append(status,registration,controls,pair,phones);
  document.querySelector('[data-panel="network"]').append(root);
  document.addEventListener('bridge-language',()=>{BridgeI18n.apply(root);if(lastStatus)render(lastStatus);});
  perform(refresh);
})();
