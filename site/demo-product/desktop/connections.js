'use strict';
const connectionSecretDraft=new Map();
const connectionSetupState=new Map();
const connectionExpanded=new Map();
function connectionLabel(mode){return t({quick:'临时 HTTPS · Cloudflare',cloudflare:'固定域名 · Cloudflare Tunnel',server:'固定域名 · 自有服务器 + SSH',nas:'固定域名 · NAS / 已有反代 / Docker'}[mode]);}
function setupGuide(link,mode){
  link.className='setup-guide';link.textContent=t('在线指引');
  link.href='https://try2love.github.io/codex-mobile-bridge/setup.html?lang='+(BridgeI18n.language()==='en'?'en':'zh')+'#'+mode;
  link.onclick=event=>{event.preventDefault();api.open('setup-'+mode).catch(e=>feedback(e.message,true));};
}
async function saveConnectionCredentials(id){
  const draft=connectionSecretDraft.get(id);
  if(!draft||!draft.changed||!Object.values(draft.secrets).some(Boolean))return;
  const submitted=JSON.stringify(draft.secrets);
  await api.connectionCredentials({id,remember:draft.remember,secrets:{...draft.secrets}});
  if(JSON.stringify(draft.secrets)===submitted)draft.changed=false;
}
async function saveAllConnectionCredentials(enabledOnly=false){
  for(const row of connectionDraft)if(!enabledOnly||row.enabled)await saveConnectionCredentials(row.id);
}
function renderConnections(){
  for(const link of document.querySelectorAll('[data-setup-guide]'))setupGuide(link,link.dataset.setupGuide);
  setupGuide($('connection-guide'),$('connection-kind').value);
  for(const card of $('connections').querySelectorAll('details.connection-card'))connectionExpanded.set(card.dataset.connectionId,card.open);
  $('connections').replaceChildren();
  if(!connectionDraft.length){const empty=document.createElement('p');empty.className='hint';empty.textContent=t('尚未添加外网连接。局域网可独立使用。');$('connections').append(empty);}
  for(const row of connectionDraft){
    const container=document.createElement('details');container.className='connection-card';container.dataset.connectionId=row.id;container.open=connectionExpanded.get(row.id)===true;
    const card=document.createElement('div');card.className='connection-body';
    const head=document.createElement('summary');head.className='section-head';
    const heading=document.createElement('h3');heading.textContent=connectionLabel(row.accessMode);
    const title=document.createElement('div');title.className='connection-title';
    const guideLink=document.createElement('a');setupGuide(guideLink,row.accessMode);title.append(heading,guideLink);
    const remove=document.createElement('button');remove.type='button';remove.textContent=t('删除');remove.dataset.removeConnection=row.id;remove.disabled=!!snapshot?.runtime.running;
    remove.onclick=()=>{connectionDraft=connectionDraft.filter(c=>c.id!==row.id);connectionSecretDraft.delete(row.id);connectionExpanded.delete(row.id);renderConnections();updateDirty();};head.append(title,remove);container.append(head,card);
    function field(key,label,type='text',placeholder='',parent=card){
      const wrap=document.createElement('label'),text=document.createElement('span'),node=document.createElement('input');text.textContent=t(label);node.type=type;node.dataset.connectionField=key;
      node.id='connection-'+row.id+'-'+key;node.placeholder=t(placeholder);node.disabled=!!snapshot?.runtime.running||busyActions.size>0;
      if(type==='checkbox'){wrap.className='check';node.checked=row[key];}else node.value=row[key]??'';
      if(type==='number'){node.min=key==='sshPort'?1:1024;node.max=65535;}
      if(key==='name')node.maxLength=100;
      if(key==='publicUrl'){node.onfocus=()=>{if(!node.value)node.value='https://';};node.onblur=()=>{if(node.value==='https://'){node.value='';row[key]='';updateDirty();}};}
      node.oninput=()=>{connectionSetupState.delete(row.id);row[key]=type==='checkbox'?node.checked:type==='number'?Number(node.value):node.value;updateDirty();};
      node.onchange=()=>{node.oninput();if(key==='enabled')renderConnections();};
      if(type==='checkbox')wrap.append(node,text);else wrap.append(text,node);parent.append(wrap);return node;
    }
    function note(text,parent=card){const node=document.createElement('p');node.className='hint';node.textContent=t(text);parent.append(node);return node;}
    function action(label,method,callback){
      const button=document.createElement('button');button.type='button';button.textContent=t(label);button.dataset.connectionAction=method;button.dataset.connection=row.id;button.dataset.key=row.id+':'+method;
      button.onclick=async()=>{
        const key=button.dataset.key;if(dirty||busyActions.size)return;busyActions.add(key);updateDirty();
        try{if(!['clearCredentials','diagnostics'].includes(method))await saveConnectionCredentials(row.id);const result=await callback();if(result?.message)feedback(result.message);}
        catch(e){showError(e);connectionSetupState.set(row.id,e.message);}
        finally{busyActions.delete(key);renderConnections();updateDirty();}
      };return button;
    }
    function secretFields(keys){
      let draft=connectionSecretDraft.get(row.id);
      if(!draft){draft={remember:true,secrets:{},changed:false,revision:0};connectionSecretDraft.set(row.id,draft);}
      for(const [key,label] of keys){
        const wrap=document.createElement('label'),text=document.createElement('span'),input=document.createElement('input');text.textContent=t(label);input.type='password';input.autocomplete='new-password';input.dataset.connectionSecret=key;
        input.id='connection-'+row.id+'-'+key;input.value=draft.secrets[key]||'';input.placeholder=t('尚未填写');input.disabled=!!snapshot?.runtime.running;
        input.oninput=()=>{draft.secrets[key]=input.value;draft.changed=true;draft.revision++;};wrap.append(text,input);card.append(wrap);secretControl(input);
      }
      const label=document.createElement('label'),check=document.createElement('input'),text=document.createElement('span');label.className='check';check.type='checkbox';check.checked=draft.remember;
      text.textContent=t('保存到系统凭据存储，重启后自动连接');check.onchange=()=>{draft.remember=check.checked;draft.changed=true;draft.revision++;};label.append(check,text);card.append(label);
      if(!draft.loaded&&snapshot?.preferences.connections.some(c=>c.id===row.id)){
        draft.loaded=true;const revision=draft.revision;
        api.readCredentials({id:row.id}).then(values=>{if(connectionSecretDraft.get(row.id)!==draft||draft.changed||draft.revision!==revision)return;draft.secrets=values;for(const [key] of keys){const input=$('connection-'+row.id+'-'+key);if(input)input.value=values[key]||'';}}).catch(error=>{draft.loaded=false;feedback(error.message,true);});
      }
      note('取消勾选时，新凭据仅本次有效；旧凭据可通过下方按钮清除。');
      const actions=document.createElement('div');actions.className='actions';
      actions.append(action('保存连接凭据','credentials',async()=>({message:t('连接凭据已保存。')})),action('清除已保存凭据','clearCredentials',async()=>{const result=await api.connectionCredentials({id:row.id,secrets:{clear:true}});draft.secrets={};draft.changed=false;draft.revision++;return result;}));card.append(actions);
    }
    field('enabled','启用此连接','checkbox');field('name','连接名称','text','可选，例如家中 NAS');
    if(row.accessMode==='quick'){
      note(snapshot?.cloudflared?.available?'已准备好 Cloudflare 程序。保存配置后启动网关，即可获取临时 HTTPS 地址。':'当前源码环境尚未准备 Cloudflare 程序，请在运行配置中安装。');
      const status=note(quickTunnelMessage());status.dataset.quickStatus='true';
      note('无需账号或域名；地址会随隧道重建变化。可在手机通知中配置入口通知。');
    }else{
      field('publicUrl','手机访问地址','text','https://codex.try2love.com');
      note('示例用户名 try2love 和域名 try2love.com 仅用于配置演示，请替换为自己的配置；示例地址不是在线演示站点。');
      if(row.accessMode==='cloudflare'){
        note('没有服务器也可以使用私人域名。先在 Cloudflare 创建固定隧道并配置公开主机名，再填写该隧道的 Token。');
        const guide=document.createElement('details'),summary=document.createElement('summary');guide.className='guide-more';summary.textContent=t('首次配置：从购买域名到手机访问');guide.append(summary);
        const steps=document.createElement('ol');steps.className='setup-steps';
        for(const text of ['在 Cloudflare 添加根域名，例如 try2love.com，取得分配给你的两条名称服务器（NS）地址。','在阿里云等域名注册商修改“DNS 服务器”为这两条 NS，等待 Cloudflare 显示 Active。域名仍在原注册商续费；已有网站和邮箱需保留原解析记录。','在 Cloudflare 的 Tunnels / 隧道中创建 Cloudflared 隧道，复制安装命令中的 Tunnel Token。这里只需要 Token，不需要整条命令或 API Key；App 已内置程序，无需重复安装服务。','在本卡片填写 https://codex.try2love.com 和 Token，启用并保存，启动网关；回到 Cloudflare 确认连接器已连接。','在隧道 Routes / 路由中添加 Published application / 公开主机名：子域名 codex，选择自己的域名，路径留空；服务类型 HTTP，服务地址使用下方电脑网关地址。保存时会创建对应 DNS 记录。','点击“检测固定入口”，再用手机关闭 Wi-Fi 后访问域名，验证登录和聊天。隧道显示 Healthy 不代表网页已经可用；电脑、Codex 和网关需要保持在线。']){const li=document.createElement('li');li.textContent=t(text);steps.append(li);}guide.append(steps);
        note('找不到域名：检查 NS、Active 状态和 DNS 缓存；1033：检查 Token 与隧道连接；502：检查电脑网关、HTTP 服务类型和端口。',guide);card.append(guide);
        note('Cloudflare 路由的服务地址（HTTP）：');
        const upstream=document.createElement('p');upstream.className='mono';upstream.textContent='http://localhost:'+($('port').value||8787);card.append(upstream);
        secretFields([['tunnelToken','Cloudflare Tunnel Token']]);
        note('Tunnel Token 用于运行已配置的隧道，不会自动创建 DNS 或修改 Cloudflare 账号。电脑和网关需保持在线。');
        const links=document.createElement('div');links.className='actions';card.append(links);
        for(const [label,target] of [['打开 Cloudflare 控制台','cloudflare-dashboard'],['固定隧道配置教程','cloudflare-domain-help']]){const button=document.createElement('button');button.type='button';button.textContent=t(label);button.onclick=()=>api.open(target).catch(e=>feedback(e.message,true));links.append(button);}
      }else if(row.accessMode==='server'){
        const wrap=document.createElement('label'),text=document.createElement('span'),select=document.createElement('select');text.textContent=t('SSH 认证方式');select.dataset.connectionField='sshAuth';select.id='connection-'+row.id+'-sshAuth';
        for(const [value,label] of [['password','账号密码'],['key','本地 SSH 私钥'],['agent','本地 SSH Agent'],['config','已有 SSH 别名 / 配置']]){const option=document.createElement('option');option.value=value;option.textContent=t(label);select.append(option);}
        select.value=row.sshAuth||'config';select.disabled=!!snapshot?.runtime.running;select.onchange=()=>{connectionSetupState.delete(row.id);row.sshAuth=select.value;renderConnections();updateDirty();};wrap.append(text,select);card.append(wrap);
        if(select.value==='config')field('sshTarget','服务器 SSH 目标','text','已有 SSH 别名，或 try2love@server.try2love.com');
        else{
          const fields=document.createElement('div');fields.className='two fields';card.append(fields);
          field('sshHost','服务器地址','text','203.0.113.10',fields);field('sshUser','SSH 用户名','text','try2love',fields);field('sshPort','SSH 端口','number','',fields);
          if(select.value==='key'){
            const key=field('sshKeyPath','本地私钥文件');const pick=document.createElement('button');pick.type='button';pick.textContent=t('选择私钥文件');pick.disabled=!!snapshot?.runtime.running;
            pick.onclick=async()=>{try{const value=await api.choose('ssh-key');if(value){connectionSetupState.delete(row.id);row.sshKeyPath=value;key.value=value;updateDirty();}}catch(e){feedback(e.message,true);}};card.append(pick);
            secretFields([['passphrase','私钥口令（可选）']]);
          }
          if(select.value==='password')secretFields([['password','SSH 密码']]);
        }
        const advanced=document.createElement('details'),summary=document.createElement('summary');summary.textContent=t('服务器高级设置');advanced.append(summary);card.append(advanced);
        field('sshRemotePort','服务器回环端口','number','',advanced);
        note('SSH 端口用于连接服务器；回环端口仅供服务器反向代理访问，通常无需修改。',advanced);
        const guide=document.createElement('details'),guideTitle=document.createElement('summary');guide.className='guide-more';guide.open=true;guideTitle.textContent=t('1. 手动准备服务器（首次配置）');guide.append(guideTitle);
        note('安装服务、修改防火墙和 SSH 权限等管理员操作，请由你或服务器管理员在终端手动完成。App 不接收 sudo 密码，也不执行服务器安装命令。',guide);
        const steps=document.createElement('ol');steps.className='setup-steps';
        for(const text of ['将自己的域名解析到服务器公网 IP；配置 HTTPS 入口所需的 DNS、防火墙和安全组。','准备普通 SSH 用户，允许远程端口转发；保持 GatewayPorts no，回环端口不要向公网开放。','手动安装并配置 HTTPS 反向代理，或接入已有站点。上游使用下方地址并保留访问域名的 Host；已有网站不要覆盖。']){const li=document.createElement('li');li.textContent=t(text);steps.append(li);}guide.append(steps);
        note('服务器 HTTPS 反向代理上游（仅服务器内部访问）：',guide);
        const upstream=document.createElement('p');upstream.className='mono';upstream.textContent='http://127.0.0.1:'+row.sshRemotePort;guide.append(upstream);
        const links=document.createElement('div');links.className='actions';guide.append(links);
        for(const [label,target] of [['HTTPS 服务配置参考','server-https-help'],['SSH 转发权限参考','server-ssh-help']]){const button=document.createElement('button');button.type='button';button.textContent=t(label);button.onclick=()=>api.open(target).catch(e=>feedback(e.message,true));links.append(button);}
        links.append(action('复制服务器检查 Prompt','diagnostics',()=>api.copyServerDiagnostics({id:row.id,language:BridgeI18n.language()})));
        card.append(guide);
        note('2. 保存上方配置，再检查 SSH 登录。检查通过仅代表账号能登录，不代表转发或公网入口可用。');
        const actions=document.createElement('div');actions.className='actions';
        async function checkHost(){
          const host=await api.serverSetup({id:row.id,action:'host'});
          if(!host.trusted){const result=await api.serverSetup({id:row.id,action:'trust',fingerprint:host.fingerprint});if(result.cancelled)throw Error(t('未保存主机指纹。'));}
        }
        actions.append(action('检查 SSH 登录','inspect',async()=>{await checkHost();const result=await api.serverSetup({id:row.id,action:'inspect'});connectionSetupState.set(row.id,result.message+(result.dnsReady?'':' '+t('尚未解析到域名地址，请先添加 DNS 记录。')));return result;}));
        card.append(actions);
        note('3. 完成手动配置后启动连接，再点击“检测固定入口”，最后用手机蜂窝网络验证登录与聊天。');
        const connectActions=document.createElement('div');connectActions.className='actions';
        connectActions.append(action('启动 SSH 连接','connect',async()=>{
          await checkHost();unwrapValidation(await api.start());await refresh();
          return {message:t('网关已启动，SSH 正在连接。请查看连接状态，再检测固定入口；启动网关会同时启动其他已启用的连接。')};
        }));card.append(connectActions);
      }else{
        field('proxyUpstream','NAS 可访问的电脑地址','text','http://192.168.1.10:'+($('port').value||8787));
        note('填写 NAS 可达的电脑 HTTP 地址，端口与网关一致。需要开启局域网访问；两端网络不通时，先建立路由或使用自有服务器 + SSH。');
      }
      const actions=document.createElement('div');actions.className='actions';
      if(row.accessMode!=='cloudflare')for(const [label,method] of [[row.accessMode==='server'?'导出手动配置参考…':'导出部署包…','exportDeployment'],[row.accessMode==='server'?'复制手动配置说明':'复制给部署 Agent','copyDeployment']])actions.append(action(label,method,()=>api[method]({id:row.id,language:BridgeI18n.language()})));
      actions.append(action('检测固定入口','checkEntry',()=>api.checkEntry({id:row.id})));card.append(actions);
      const status=note([connectionSetupState.get(row.id),snapshot?.externalStatus?.[row.id]?.message].filter(Boolean).map(t).join(' ')||'保存配置后可检测入口。');status.dataset.connectionStatus=row.id;
    }
    $('connections').append(container);
  }
  BridgeI18n.apply();
}
function addConnection(){
  if(snapshot?.runtime.running)return;
  connectionDraft.push({id:crypto.randomUUID(),name:'',enabled:true,accessMode:$('connection-kind').value,publicUrl:'',sshTarget:'',sshRemotePort:18787,proxyUpstream:'',sshAuth:'password',sshHost:'',sshUser:'',sshPort:22,sshKeyPath:''});
  connectionExpanded.set(connectionDraft[connectionDraft.length-1].id,true);
  renderConnections();updateDirty();
}
