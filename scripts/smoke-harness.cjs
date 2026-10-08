// Real Electron UI + private controller + optional official Harness. Isolated data only.
'use strict';
const {app}=require('electron');
const fs=require('node:fs'),path=require('node:path'),net=require('node:net'),assert=require('node:assert/strict');
if(!process.env.CMB_DATA_DIR||!process.env.CMB_HARNESS_EXECUTABLE)throw Error('Set isolated CMB_DATA_DIR and CMB_HARNESS_EXECUTABLE');
const dataDir=path.resolve(process.env.CMB_DATA_DIR);
if(fs.existsSync(path.join(dataDir,'desktop.json')))throw Error('Use a fresh test data directory');
fs.mkdirSync(dataDir,{recursive:true});
const portServer=net.createServer();
portServer.listen(0,'127.0.0.1',()=>{
  const port=portServer.address().port;portServer.close(()=>{
    fs.writeFileSync(path.join(dataDir,'desktop.json'),JSON.stringify({port,lan:false,localAccess:true,connections:[],codexHome:dataDir,ipcPath:path.join(dataDir,'fixture.sock')}),{mode:0o600});
    app.on('browser-window-created',(_,win)=>{
      win.webContents.once('did-finish-load',async()=>{
        const evaluate=code=>win.webContents.executeJavaScript(code);
        async function until(code){const deadline=Date.now()+90000;while(Date.now()<deadline){if(await evaluate(code))return;await new Promise(r=>setTimeout(r,200));}throw Error('UI timeout: '+code);}
        try{
          await until('Boolean(snapshot)');
          await evaluate("document.querySelector('[data-tab=harness]').click()");
          await until('Boolean(harnessPanel.value)');
          await evaluate(`(()=>{const root=document.getElementById('harness-panel');const values=${JSON.stringify({executable:process.env.CMB_HARNESS_EXECUTABLE,workspace:dataDir,home:path.join(dataDir,'harness-home')})};for(const [key,value] of Object.entries(values)){const input=root.querySelector('[name='+key+']');input.value=value;input.dispatchEvent(new Event('input',{bubbles:true}));}root.querySelector('form').requestSubmit();})()`);
          await until('!harnessPanel.busy&&!harnessPanel.dirty');
          assert.equal(JSON.parse(fs.readFileSync(path.join(dataDir,'harness.json'))).workspace,dataDir);
          await evaluate("document.getElementById('start').click()");
          await until('snapshot.runtime.running');
          await evaluate("document.querySelector('[data-harness-action=start]').click()");
          await until('!harnessPanel.busy&&harnessPanel.value.running');
          assert.equal(await evaluate("document.querySelector('[data-harness-action=start]').disabled"),true);
          assert.equal(await evaluate("document.querySelector('[data-harness-action=open]').disabled"),false);
          fs.writeFileSync(path.join(dataDir,'harness-desktop.png'),(await win.webContents.capturePage()).toPNG());
          await evaluate("document.querySelector('[data-harness-action=stop]').click()");
          await until('!harnessPanel.busy&&!harnessPanel.value.running');
          await evaluate('window.bridgeDesktop.stop()');
          console.log(JSON.stringify({ok:true,checks:['real Electron panel','private IPC save','gateway start','Harness start and auth handshake','running button state','Harness stop','gateway stop']}));
          app.exit(0);
        }catch(error){console.error(error);try{await evaluate('window.bridgeDesktop.stop()');}catch{}app.exit(1);}
      });
    });
    require('../desktop/main.cjs');
  });
});
