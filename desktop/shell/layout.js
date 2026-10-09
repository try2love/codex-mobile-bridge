'use strict';
// Preserve the existing forms and handlers while presenting each module as a compact overview.
(() => {
  const $=id=>document.getElementById(id),t=BridgeI18n.t;
  const text=(tag,key,cls='')=>{const n=document.createElement(tag);n.className=cls;n.dataset.i18n=key;n.textContent=t(key);return n;};
  const module=name=>{const n=document.createElement('section');n.dataset.panel=name;n.hidden=true;$('settings').insertBefore(n,$('save-bar'));return n;};
  const paths={overview:'M3 3h7v7H3z M14 3h7v7h-7z M3 14h7v7H3z M14 14h7v7h-7z',accounts:'M3 4h18v13H3z M8 21h8 M12 17v4',network:'M10 13a5 5 0 0 0 7 0l3-3a5 5 0 0 0-7-7l-2 2 M14 11a5 5 0 0 0-7 0l-3 3a5 5 0 0 0 7 7l2-2',devices:'M12 3 3 7v5c0 5 9 10 9 10s9-5 9-10V7z M8 12l3 3 5-6',notifications:'M4 4h16v13H9l-5 4z',advanced:'M4 7h16 M4 17h16 M8 4v6 M16 14v6'};
  document.querySelector('.brand').innerHTML='<img src="assets/icon.png" alt=""><span>Bridge<small>DESKTOP GATEWAY</small></span>';
  document.querySelector('.sidebar-caption').remove();
  for(const [index,button] of [...document.querySelectorAll('aside [data-tab]')].entries()){
    if([0,2,5].includes(index))button.before(text('div',index===0?'工作台':index===2?'访问与提醒':'应用','nav-group'));
    const label=button.dataset.i18n;if(label){button.removeAttribute('data-i18n');button.replaceChildren(text('span',label));}
    const svg=document.createElementNS('http://www.w3.org/2000/svg','svg');svg.setAttribute('viewBox','0 0 24 24');svg.setAttribute('aria-hidden','true');const path=document.createElementNS(svg.namespaceURI,'path');path.setAttribute('d',paths[button.dataset.tab]);svg.append(path);button.prepend(svg);
  }
  const descriptions={overview:'查看网关状态与可用访问入口。',accounts:'管理各客户端的连接、账号与工作区。',network:'设置本机、局域网与外网访问入口。',devices:'管理网关登录、受信任设备与访问保护。',notifications:'配置推送通道、通知事件与跳转方式。',advanced:'管理应用偏好、版本与运行诊断。'};
  const description=text('p',descriptions.overview,'module-description');document.querySelector('main > header').after(description);
  module('devices').append($('auth-mode').closest('article'));
  module('network').append($('cloudflare-setup'));
  const codexPaths=module('accounts');codexPaths.append($('codex-home').closest('article'));codexPaths.dataset.clientConfig='codex';
  const accounts=$('accounts-content').closest('section');accounts.dataset.clientConfig='codex';
  const back=text('button','返回客户端列表','client-config-back');back.type='button';accounts.before(back);back.hidden=true;
  const native=$('desktop-connections');for(const [index,article] of [...native.querySelectorAll(':scope > article')].entries())article.dataset.clientConfig=index===0?'deepseek':'claude';
  $('harness-panel').dataset.clientConfig='deepseek';
  // Secondary access mode and advanced paths remain available inside the selected client.
  function compact(article,label){const details=document.createElement('details');details.className='module-setting';const heading=article.querySelector('h2,h3,summary');const summary=text('summary',label||heading?.dataset.i18n||heading?.textContent||'设置');article.before(details);details.append(summary,article);return details;}
  compact($('harness-panel').querySelector('article'),'官方 Web');
  const clients=document.createElement('section');clients.dataset.panel='accounts';clients.id='clients-page';clients.hidden=true;
  clients.innerHTML='<div class="gateway-banner"><span class="gateway-indicator" aria-hidden="true">●</span><div><strong data-gateway-status></strong><small data-i18n="网页与手机使用相同的应用启用设置">网页与手机使用相同的应用启用设置</small></div><div class="actions"><button type="button" class="primary" data-open-workbench data-i18n="打开工作台 ↗">打开工作台 ↗</button><button type="button" id="clients-start" data-i18n="启动网关" disabled>启动网关</button><button type="button" id="clients-stop" data-i18n="停止" disabled>停止</button></div></div><div class="section-head"><h2 data-i18n="本机客户端">本机客户端</h2><button type="button" id="refresh-clients" data-i18n="重新扫描并接入">重新扫描并接入</button></div><p id="client-scan-status" class="client-scan-status" role="status"></p><div id="client-overview"></div><p class="hint" data-i18n="自动发现本机应用并沿用已有登录或 API；安装新应用后可重新扫描。">自动发现本机应用并沿用已有登录或 API；安装新应用后可重新扫描。</p><div class="two client-notes"><article><h3 data-i18n="工作区">工作区</h3><p data-i18n="各客户端独立管理项目与会话。新建会话使用当前客户端的项目配置。">各客户端独立管理项目与会话。新建会话使用当前客户端的项目配置。</p></article><article><h3 data-i18n="接入设置">接入设置</h3><div class="cap-row"><span>Codex</span><small data-i18n="账号 / API · 额度 · IPC">账号 / API · 额度 · IPC</small></div><div class="cap-row"><span>Claude</span><small data-i18n="连接方式 · 会话范围">连接方式 · 会话范围</small></div><div class="cap-row"><span>DSH</span><small data-i18n="连接 · 工作目录 · 官方 Web">连接 · 工作目录 · 官方 Web</small></div></article></div>';
  $('settings').before(clients);
  let client=null;
  function selectClient(id){client=id;native.classList.toggle('client-config-hidden',!['claude','deepseek'].includes(id));back.hidden=!id;clients.classList.toggle('configuration-open',!!id);document.querySelectorAll('[data-client-config]').forEach(n=>n.classList.toggle('client-config-hidden',n.dataset.clientConfig!==id));if(id)document.querySelector('[data-client-config="'+id+'"]')?.scrollIntoView({block:'start'});document.dispatchEvent(new CustomEvent('bridge-client-config',{detail:id}));}
  back.onclick=()=>selectClient(null);selectClient(null);
  // Keep device controls visible; connection and notification details stay compact.
  for(const name of ['network','devices','notifications'])for(const article of document.querySelectorAll('[data-panel="'+name+'"] > article'))compact(article).open=name==='devices';
  const notificationPage=document.querySelector('[data-panel="notifications"]');
  const groups=[['推送通道',['bark-enabled','ntfy-enabled','pushplus-enabled','mobile-enabled']],['通知事件',['security-enabled','address-enabled','watches']],['内容与跳转',['click-base']],['帮助与测试',[]]];
  for(const [title,ids] of groups){const group=document.createElement('div');group.className='module-group';group.append(text('h2',title));notificationPage.append(group);for(const id of ids){const setting=$(id)?.closest('.module-setting');if(setting)group.append(setting);}if(!ids.length)for(const setting of notificationPage.querySelectorAll(':scope > .module-setting'))group.append(setting);}
  const maintenance=document.querySelector('[data-panel="advanced"]');const maintenanceTabs=document.createElement('div');maintenanceTabs.className='maintenance-tabs';
  for(const [key,label] of [['general','常规'],['updates','更新'],['logs','日志与诊断']]){const b=text('button',label);b.type='button';b.onclick=()=>maintenanceTab(key);b.dataset.maintenanceTab=key;maintenanceTabs.append(b);}maintenance.before(maintenanceTabs);
  function maintenanceTab(key){maintenance.hidden=key!=='general';for(const name of ['updates','logs'])document.querySelector('[data-panel="'+name+'"]').hidden=name!==key;maintenanceTabs.querySelectorAll('button').forEach(b=>b.classList.toggle('active',b.dataset.maintenanceTab===key));}
  const hero=document.querySelector('.hero');hero.classList.add('gateway-banner');hero.querySelector('.hero-icon').textContent='●';hero.querySelector('h2').replaceChildren(text('span','网关状态'));hero.querySelector('p').replaceChildren(text('span','在网页或手机上继续各客户端的会话。'));
  const open=text('button','打开工作台 ↗','primary');open.type='button';open.dataset.openWorkbench='';hero.querySelector('.actions').prepend(open);$('start').classList.remove('primary');
  const overview=document.querySelector('[data-panel="overview"]');overview.querySelector('[data-jump="accounts"]').remove();
  const addresses=$('addresses');addresses.classList.add('address-summary');const entrances=document.createElement('article');entrances.className='entrances';addresses.previousElementSibling.before(entrances);entrances.append(addresses.previousElementSibling,addresses,$('quick-tunnel-state'),$('quick-setup'));
  const footer=document.createElement('div');footer.className='entrance-footer';const more=text('button','展开全部入口');more.id='more-addresses';more.type='button';more.onclick=()=>{const expanded=addresses.classList.toggle('expanded');more.dataset.i18n=expanded?'收起入口':'展开全部入口';more.textContent=t(more.dataset.i18n);};const network=text('button','管理网络访问 →');network.type='button';network.dataset.jump='network';footer.append(more,network);entrances.append(footer);new MutationObserver(()=>more.hidden=addresses.children.length<=3).observe(addresses,{childList:true});
  let state;
  document.querySelectorAll('[data-open-workbench]').forEach(b=>b.onclick=()=>{const url=state?.urls?.find(url=>url.startsWith('https:'))||state?.urls?.[0];if(url)window.bridgeDesktop.open(url);});
  window.GatewayLayout={selectClient,tab(name){description.dataset.i18n=descriptions[name];description.textContent=t(descriptions[name]);back.hidden=name!=='accounts'||!client;maintenanceTabs.hidden=name!=='advanced';if(name==='advanced')maintenanceTab('general');},update(value,{starting=false,stopping=false}={}){state=value;document.querySelectorAll('[data-gateway-status]').forEach(n=>n.textContent=t(stopping?'正在停止网关':starting?'正在启动网关':value.runtime.running?'网关运行中':'网关未启动'));document.querySelectorAll('[data-open-workbench]').forEach(b=>b.disabled=!value.runtime.running||!value.urls?.length);}};
})();
