// Real isolated Electron renderer and private IPC; no external server or account.
'use strict';
const {app}=require('electron');
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
if(!process.env.CMB_DATA_DIR||!path.resolve(process.env.CMB_DATA_DIR).includes(path.sep+'.tmp'+path.sep))throw Error('Use an isolated project .tmp directory');
app.on('browser-window-created',(_,win)=>{
  win.webContents.once('did-finish-load',async()=>{
    const evaluate=code=>win.webContents.executeJavaScript(code);
    async function until(code){const end=Date.now()+15000;while(Date.now()<end){if(await evaluate(code))return;await new Promise(r=>setTimeout(r,150));}throw Error('Timeout: '+code);}
    try{
      await until('Boolean(snapshot)');
      assert.equal(await evaluate('snapshot.notifications.addressEnabled'),true);
      assert.equal(await evaluate('snapshot.cloudflared.available'),true);
      await evaluate("tab('network');$('connection-kind').value='server';addConnection()");
      assert.equal(await evaluate("document.querySelector('[data-connection-field=sshAuth]').value"),'password');
      assert.ok(await evaluate("document.querySelector('[data-connection-field=sshPort]')"));
      await evaluate("Object.assign(connectionDraft[0],{enabled:false,publicUrl:'https://codex.example.com',sshHost:'127.0.0.1',sshUser:'ubuntu',sshPort:2222});renderConnections();updateDirty();$('settings').requestSubmit()");
      await until('!dirty');
      assert.equal(await evaluate('snapshot.preferences.connections[0].sshPort'),2222);
      await win.setSize(1100,900);
      await evaluate("document.querySelector('.connection-card').scrollIntoView({block:'start',behavior:'instant'})");
      fs.writeFileSync(path.join(process.env.CMB_DATA_DIR,'server-setup-zh.png'),(await win.webContents.capturePage()).toPNG());
      await evaluate("$('connection-kind').value='cloudflare';addConnection();Object.assign(connectionDraft[1],{enabled:false,publicUrl:'https://phone.example.com'});renderConnections();updateDirty();$('settings').requestSubmit()");
      await until('!dirty');
      await evaluate("document.querySelectorAll('.connection-card')[1].scrollIntoView({block:'start',behavior:'instant'})");
      assert.match(await evaluate("document.querySelector('[data-panel=network]').innerText"),/localhost:/);
      fs.writeFileSync(path.join(process.env.CMB_DATA_DIR,'cloudflare-domain-zh.png'),(await win.webContents.capturePage()).toPNG());
      const secret='only-a-local-test-token';
      await evaluate(`api.connectionCredentials({id:connectionDraft[1].id,remember:false,secrets:{tunnelToken:${JSON.stringify(secret)}}})`);
      assert.ok(!fs.readFileSync(path.join(process.env.CMB_DATA_DIR,'desktop.json'),'utf8').includes(secret));
      assert.ok(!fs.readFileSync(path.join(process.env.CMB_DATA_DIR,'config.json'),'utf8').includes(secret));
      await evaluate("applyLanguage('en');tab('network');document.querySelectorAll('.connection-card')[1].scrollIntoView({block:'start',behavior:'instant'})");
      assert.match(await evaluate("document.querySelector('[data-panel=network]').innerText"),/Fixed domain · Cloudflare Tunnel/);
      fs.writeFileSync(path.join(process.env.CMB_DATA_DIR,'cloudflare-domain-en.png'),(await win.webContents.capturePage()).toPNG());
      await evaluate("tab('overview');$('start').click()");
      await until('snapshot.runtime.running');
      await evaluate("$('stop').click()");
      await until('!snapshot.runtime.running');
      console.log(JSON.stringify({ok:true,checks:['first-run notification default','bundled dependency detection','SSH fields and save','named domain setup','session secret excluded from config','Chinese and English UI','isolated gateway start and stop']}));
      app.exit(0);
    }catch(error){console.error(error);try{await evaluate('api.stop()');}catch{}app.exit(1);}
  });
});
require('../desktop/main.cjs');
