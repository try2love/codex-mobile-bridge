// Isolated real Electron IPC/renderer regression. No Cloudflare account or remote server.
'use strict';
const {app,dialog,shell}=require('electron');
const openedGuides=[];shell.openExternal=async url=>{openedGuides.push(url);};
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {runWorker}=require('../desktop/controller.cjs');
const root=path.resolve(__dirname,'..');
const data=fs.mkdtempSync(path.join(root,'.tmp','setup-feedback-'));
process.env.CMB_DATA_DIR=data;
const worker=(action,payload)=>runWorker({executable:process.env.CMB_PYTHON||'python3',prefix:[path.join(root,'desktop.py')],dataDir:data},action,payload);
let picker;
dialog.showOpenDialog=async(_window,options)=>{picker=options;return {canceled:true,filePaths:[]};};
app.on('browser-window-created',(_,win)=>{
  win.webContents.once('did-finish-load',async()=>{
    let step=0;const evaluate=async code=>{const current=++step;try{return await win.webContents.executeJavaScript(code);}catch(error){throw Error('Evaluation '+current+': '+error.message);}};
    async function until(code){const end=Date.now()+20000;while(Date.now()<end){try{if(await evaluate(code))return;}catch{}await new Promise(r=>setTimeout(r,150));}throw Error('Timeout: '+code);}
    const save=async()=>{await evaluate("$('settings').requestSubmit()");await until('!saving&&!dirty');};
    try{
      await until('Boolean(snapshot)&&Boolean(credentialLoad)');await evaluate('credentialLoad');
      for(const language of ['zh','en']){
        await evaluate("$('language').value='"+language+"';$('language').onchange()");
        await evaluate("tab('network')");
        for(const mode of ['quick','cloudflare','server','nas']){
          await evaluate("$('connection-kind').value='"+mode+"';$('connection-kind').onchange();$('connection-guide').click();addConnection()");
          assert.equal(openedGuides.at(-1),'https://try2love.github.io/codex-mobile-bridge/setup.html?lang='+language+'#'+mode);
          await evaluate("document.querySelector('.connection-card:last-child .setup-guide').click()");
          assert.equal(openedGuides.at(-1),'https://try2love.github.io/codex-mobile-bridge/setup.html?lang='+language+'#'+mode);
        }
        assert.equal(await evaluate("[...document.querySelectorAll('.connection-card .setup-guide')].every(a=>a.textContent==='"+(language==='en'?'Online guide':'在线指引')+"')"),true);
        await evaluate("document.querySelector('[data-setup-guide=lan]').click()");
        assert.equal(openedGuides.at(-1),'https://try2love.github.io/codex-mobile-bridge/setup.html?lang='+language+'#lan');
        await evaluate("tab('network');document.querySelector('.connection-card').scrollIntoView({block:'start',behavior:'instant'});new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)))");
        fs.writeFileSync(path.join(data,'connection-guides-'+language+'.png'),(await win.webContents.capturePage()).toPNG());
        await evaluate("connectionDraft=[];connectionSecretDraft.clear();renderConnections();updateDirty()");
      }
      await evaluate("$('language').value='zh';$('language').onchange()");
      await evaluate("tab('network');connectionDraft=['quick','cloudflare','server','nas'].map((accessMode,i)=>({id:'fold-'+i,accessMode,enabled:false,sshAuth:'key',sshRemotePort:18787}));connectionExpanded.clear();renderConnections();updateDirty()");
      assert.equal(await evaluate("document.querySelectorAll('details.connection-card:not([open])').length"),4);
      assert.equal(await evaluate("document.querySelectorAll('details.connection-basic:not([open])').length"),2);
      assert.ok(await evaluate("parseFloat(getComputedStyle(document.querySelector('.connection-card summary'),'::before').fontSize)>=20"));
      await evaluate("document.querySelectorAll('.connection-card')[1].open=true;new Promise(r=>requestAnimationFrame(r))");
      win.show();win.focus();win.webContents.focus();
      await until('document.hasFocus()');
      await evaluate("document.querySelectorAll('[data-connection-field=publicUrl]')[0].focus()");
      assert.equal(await evaluate("document.activeElement.value"),'https://',JSON.stringify(await evaluate("({active:document.activeElement.id,field:document.querySelector('[data-connection-field=publicUrl]').value,disabled:document.querySelector('[data-connection-field=publicUrl]').disabled,panel:activeTab,open:document.querySelectorAll('.connection-card')[1].open})")));
      await evaluate("document.activeElement.blur()");
      assert.equal(await evaluate("document.querySelectorAll('[data-connection-field=publicUrl]')[0].value"),'');

      await evaluate("document.querySelector('.connection-card summary').click()");
      assert.equal(await evaluate("document.querySelector('.connection-card').open"),true);
      await evaluate("renderConnections()");
      assert.equal(await evaluate("document.querySelector('.connection-card').open"),true);
      await evaluate("document.querySelector('.connection-card summary').click();renderConnections()");
      assert.equal(await evaluate("document.querySelector('.connection-card').open"),false);
      await evaluate("document.querySelector('.connection-card .setup-guide').click()");
      assert.equal(await evaluate("document.querySelector('.connection-card').open"),false);
      await evaluate("new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)))");
      fs.writeFileSync(path.join(data,'collapsed-connections.png'),(await win.webContents.capturePage()).toPNG());
      await evaluate("connectionDraft=[];connectionExpanded.clear();connectionSecretDraft.clear();renderConnections();updateDirty()");


      await evaluate("tab('network');$('connection-kind').value='cloudflare';addConnection();connectionDraft[0].publicUrl='https://first.example.com';addConnection();connectionDraft[1].publicUrl='https://wrong.example.com/path';renderConnections();updateDirty();document.querySelectorAll('details.connection-card').forEach(card=>card.open=false);$('settings').requestSubmit()");
      await until("!saving&&!$('toast').hidden");
      assert.equal(await evaluate('document.activeElement.id'),await evaluate("'connection-'+connectionDraft[1].id+'-publicUrl'"));
      assert.equal(await evaluate("document.activeElement.closest('details.connection-card').open"),true);
      assert.match(await evaluate("$('toast-message').textContent"),/完整 HTTPS/);
      assert.equal(await evaluate("getComputedStyle($('toast')).position"),'fixed');
      assert.equal(await evaluate("$('toast-progress').getAnimations()[0].effect.getTiming().duration"),5000);
      fs.writeFileSync(path.join(data,'validation-focus.png'),(await win.webContents.capturePage()).toPNG());
      await evaluate("$('toast-close').click()");assert.equal(await evaluate("$('toast').hidden"),true);
      await evaluate("connectionDraft.pop();renderConnections();updateDirty()");await save();
      await evaluate("{const draft=connectionSecretDraft.get(connectionDraft[0].id);draft.remember=false;const input=document.querySelector('[data-connection-secret=tunnelToken]');input.value='fixture-token';input.dispatchEvent(new Event('input',{bubbles:true}));$('settings').requestSubmit()}");
      await until('!saving');
      assert.equal(await evaluate("document.querySelector('[data-connection-secret=tunnelToken]').value==='fixture-token'"),true);
      assert.equal(await evaluate("document.querySelector('[data-connection-secret=tunnelToken]').type"),'password');
      await evaluate("document.querySelector('[data-connection-secret=tunnelToken]').nextElementSibling.click()");
      assert.equal(await evaluate("document.querySelector('[data-connection-secret=tunnelToken]').type"),'text');
      await evaluate("renderConnections();tab('overview');$('start').click()");await until('snapshot.runtime.running');
      assert.ok(!/SSH 密码/.test(await evaluate("$('toast-message').textContent")));
      await evaluate("$('stop').click()");await until('!snapshot.runtime.running');
      // Renderer reload must reload session-only credentials via the real main-process IPC.
      await evaluate('setTimeout(()=>location.reload(),0)');await until('Boolean(snapshot)');await until("document.querySelector('[data-connection-secret=tunnelToken]')?.value==='fixture-token'");
      await evaluate("tab('network');$('connection-kind').value='server';addConnection();Object.assign(connectionDraft[1],{enabled:false,sshAuth:'key'});renderConnections();updateDirty()");
      await evaluate("[...document.querySelectorAll('button')].find(node=>node.textContent==='选择私钥文件').click()");
      await until('true');assert.ok(picker.properties.includes('showHiddenFiles'));assert.equal(path.basename(picker.defaultPath),'.ssh');
      await save();
      assert.equal(await evaluate("document.querySelector('[data-connection-action=inspect]').disabled"),false);
      assert.equal(await evaluate("document.querySelector('[data-connection-action=connect]').disabled"),true);
      await evaluate("tab('notifications');$('ntfy-token').value='fixture-ntfy';$('bark-key').value='fixture-bark';$('pushplus-token').value='fixture-push';updateDirty()");await save();
      assert.equal(await evaluate("$('ntfy-token').value==='fixture-ntfy'&&$('bark-key').value==='fixture-bark'&&$('pushplus-token').value==='fixture-push'"),true);
      // Showing a saved credential must not submit it again when a destination changes.
      assert.equal(await evaluate("collect().notifications.token===''&&collect().notifications.barkKey===''"),true);
      await evaluate('setTimeout(()=>location.reload(),0)');await until("Boolean(snapshot)&&$('ntfy-token').value==='fixture-ntfy'");
      await evaluate("tab('notifications');$('clear-token').checked=true;updateDirty()");await save();
      assert.equal(await evaluate("$('ntfy-token').value"),'');
      await evaluate("tab('network');document.querySelector('[data-connection-action=clearCredentials]').click()");
      await until("!busyActions.size&&document.querySelector('[data-connection-secret=tunnelToken]').value===''");
      await evaluate("feedback('SSH 认证和服务器预检通过。请确认域名 DNS 指向该服务器，且公网 80/443 可达。')");
      fs.writeFileSync(path.join(data,'ssh-result-toast.png'),(await win.webContents.capturePage()).toPNG());
      await new Promise(r=>setTimeout(r,5300));assert.equal(await evaluate("$('toast').hidden"),true);
      console.log(JSON.stringify({ok:true,data,checks:['each connection has a direct bilingual tutorial link','exact invalid connection focused','fixed dismissible 5s toast','saved secrets masked and revealable','Cloudflare-only start without SSH password','renderer reload retains secrets','notification secrets retained and cleared','hidden SSH folder picker','toast expiry']}));
      app.exit(0);
    }catch(error){console.error(error);try{await worker('stop');}catch{}app.exit(1);}
  });
});
(async()=>{
  const net=require('node:net'),server=net.createServer();await new Promise(r=>server.listen(0,'127.0.0.1',r));const port=server.address().port;await new Promise(r=>server.close(r));
  const initial=await worker('snapshot');
  // A no-op connector tests launch validation without creating an external tunnel.
  initial.preferences={...initial.preferences,port,lan:false,codexHome:data,ipcPath:path.join(data,'missing.sock'),cloudflared:'/usr/bin/true',connections:[]};
  await worker('save',initial);require('../desktop/main.cjs');
})().catch(error=>{console.error(error);app.exit(1);});
