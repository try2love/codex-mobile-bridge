// End-to-end QR rendering + phone browser login using synthetic, isolated data.
'use strict';
const {app,BrowserWindow}=require('electron');
const fs=require('node:fs'),path=require('node:path'),net=require('node:net'),assert=require('node:assert/strict');
const {runWorker,workerFor}=require('../desktop/controller.cjs');
const {PNG}=require('pngjs'),decode=require('jsqr');
const root=path.resolve(__dirname,'..');
fs.mkdirSync(path.join(root,'.tmp'),{recursive:true});
const data=fs.mkdtempSync(path.join(root,'.tmp','pairing-browser-'));
process.env.CMB_DATA_DIR=data;
app.setPath('userData',path.join(data,'browser'));
const workerConfig=process.env.CMB_TEST_RUNTIME?{executable:process.env.CMB_TEST_RUNTIME,dataDir:data}:workerFor({root,dataDir:data,packaged:false});
const worker=(action,payload)=>runWorker(workerConfig,action,payload);
async function freePort(){const server=net.createServer();await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));const port=server.address().port;await new Promise(resolve=>server.close(resolve));return port;}
async function until(fn,label){for(let i=0;i<60;i++){if(await fn())return;await new Promise(resolve=>setTimeout(resolve,200));}throw Error('Timed out: '+label);}
function decodeImage(dataUrl){const image=PNG.sync.read(Buffer.from(dataUrl.split(',')[1],'base64'));return decode(new Uint8ClampedArray(image.data),image.width,image.height).data;}
app.whenReady().then(async()=>{
  let desktop,phone,nativePhone,started=false,exitCode=0;
  try{
    const value=await worker('snapshot');
    value.preferences={...value.preferences,port:await freePort(),lan:true,tunnel:false,connections:[],autoStart:false,codexHome:data,ipcPath:path.join(data,'unavailable.sock')};
    value.auth.password='isolated-qr-test-password';await worker('save',value);
    await worker('start');started=true;await until(async()=>(await worker('snapshot')).runtime.running,'gateway');
    desktop=new BrowserWindow({show:false,width:900,height:1000,webPreferences:{preload:path.join(__dirname,'smoke-pairing-preload.cjs'),contextIsolation:true,nodeIntegration:false,sandbox:false}});
    const evaluate=code=>desktop.webContents.executeJavaScript(code);
    await desktop.loadFile(path.join(root,'desktop/index.html'));
    await until(()=>evaluate('Boolean(snapshot?.runtime.running&&document.querySelector(".qr-entry"))'),'address entries');
    assert.equal(await evaluate('[...document.querySelectorAll(".qr-entry")].every(n=>!n.open)'),true);
    assert.equal(await evaluate('document.getElementById("error").hidden'),true,'No startup error');
    await evaluate('document.querySelector(".qr-entry").open=true');
    await until(()=>evaluate('Boolean(document.querySelector(".qr-entry img[src]"))'),'QR image');
    const grant=await evaluate('({image:document.querySelector(".qr-entry img").src,id:[...qrEntries.values()][0].grant.id})');
    const url=decodeImage(grant.image);
    assert.ok(url.includes('/#pair='));assert.ok(!url.includes('password'));
    // Snapshot polling must keep expanded state and the same usable grant.
    await evaluate('refresh()');
    assert.equal(await evaluate('document.querySelector(".qr-entry").open'),true);
    assert.equal(await evaluate('[...qrEntries.values()][0].grant.id'),grant.id);
    await evaluate('applyLanguage("en")');
    assert.equal(await evaluate('document.querySelector(".qr-entry summary").textContent'),'Scan to sign in');
    assert.equal(await evaluate('[...qrEntries.values()][0].grant.id'),grant.id);
    assert.equal(await evaluate('document.documentElement.scrollWidth>innerWidth'),false);
    // Refresh invalidates the old grant before handing out another image.
    await evaluate('document.querySelector(".qr-entry button").click()');
    await until(()=>evaluate('Boolean([...qrEntries.values()][0].grant&&[...qrEntries.values()][0].grant.id!=='+JSON.stringify(grant.id)+')'),'QR refresh');
    assert.equal((await worker('pairing',{action:'status',id:grant.id})).state,'expired');
    const current=await evaluate('({image:document.querySelector(".qr-entry img").src,id:[...qrEntries.values()][0].grant.id})');
    const currentUrl=decodeImage(current.image);
    const expandedImage=(await desktop.capturePage()).toPNG();
    phone=new BrowserWindow({show:false,width:390,height:844,webPreferences:{partition:'pairing-'+Date.now(),contextIsolation:true,nodeIntegration:false,sandbox:true}});
    let leaked=false;
    phone.webContents.session.webRequest.onBeforeRequest((details,callback)=>{if(new URL(details.url).search.includes('pair='))leaked=true;callback({});});
    await phone.loadURL(currentUrl);
    const mobile=code=>phone.webContents.executeJavaScript(code);
    await until(()=>mobile('document.getElementById("login").hidden&&!document.getElementById("app").hidden&&Boolean(csrf)'),'automatic phone login');
    assert.equal(await mobile('location.hash'),'');
    assert.equal(await mobile('fetch("/api/auth").then(r=>r.json()).then(v=>v.authenticated)'),true);
    assert.equal(await mobile('document.documentElement.scrollWidth>innerWidth'),false);
    assert.equal(leaked,false,'Bearer token must not appear in URL query parameters');
    assert.equal((await worker('pairing',{action:'status',id:current.id})).state,'used');
    await mobile('document.getElementById("logout").click()');
    await until(()=>mobile('!document.getElementById("login").hidden'),'logout');
    await phone.loadURL(currentUrl);
    await until(()=>mobile('Boolean(document.getElementById("login-error").textContent)'),'replay rejected');
    assert.equal(await mobile('document.getElementById("login").hidden&&!document.getElementById("app").hidden&&Boolean(csrf)'),false);
    await mobile('document.getElementById("username").value="admin";document.getElementById("password").value="isolated-qr-test-password";document.getElementById("login-form").requestSubmit()');
    await until(()=>mobile('document.getElementById("login").hidden&&!document.getElementById("app").hidden&&Boolean(csrf)'),'password login fallback');
    // Collapsing cancels a fresh, unused code and hides its pixels.
    await evaluate('document.querySelector(".qr-entry button").click()');
    await until(()=>evaluate('Boolean([...qrEntries.values()][0].grant?.state==="active")'),'fresh code');
    const collapseId=await evaluate('[...qrEntries.values()][0].grant.id');
    await evaluate('document.querySelector(".qr-entry").open=false');
    await until(async()=>(await worker('pairing',{action:'status',id:collapseId})).state==='expired','collapse revocation');
    assert.equal(await evaluate('document.querySelector(".qr-entry img").hasAttribute("src")'),false);
    // The native shells use a persistent WebView store and this UA suffix.
    // Exercise their gateway contract without a real phone or account.
    for(const platform of ['Android','iOS']){
      const partition='persist:pairing-native-'+platform;
      const openNative=()=>{
        const view=new BrowserWindow({show:false,width:390,height:844,webPreferences:{partition,contextIsolation:true,nodeIntegration:false,sandbox:true}});
        view.webContents.setUserAgent('Mozilla/5.0 BridgeMobile/0.1-'+platform);return view;
      };
      const entry=(await worker('snapshot')).urls.find(url=>new URL(url).hostname===new URL(currentUrl).hostname);
      const fresh=await worker('pairing',{action:'create',url:entry});
      nativePhone=openNative();await nativePhone.loadURL(fresh.url);
      const native=code=>nativePhone.webContents.executeJavaScript(code);
      await until(()=>native('document.getElementById("login").hidden&&!document.getElementById("app").hidden&&Boolean(csrf)'),platform+' QR sign-in');
      assert.equal(await native('fetch("/api/auth").then(r=>r.json()).then(v=>v.trustedDevice)'),true);
      const store=nativePhone.webContents.session.cookies;
      const cookie=(await store.get({url:entry,name:'codex_mobile_session'}))[0];
      assert.ok(cookie.httpOnly&&!cookie.session&&cookie.expirationDate>Date.now()/1000+399*86400);
      await store.flushStore();nativePhone.destroy();nativePhone=null;
      await worker('stop');await worker('start');await until(async()=>(await worker('snapshot')).runtime.running,'restarted gateway');
      nativePhone=openNative();await nativePhone.loadURL(entry);
      await until(()=>native('document.getElementById("login").hidden&&!document.getElementById("app").hidden&&Boolean(csrf)'),platform+' retained binding');
      // An old/used QR cannot log an already bound device out.
      await nativePhone.loadURL(fresh.url);
      await until(()=>native('document.getElementById("login").hidden&&!document.getElementById("app").hidden&&Boolean(csrf)'),platform+' expired QR retains binding');
      const device=(await worker('devices',{action:'list'})).sessions.find(row=>row.userAgent.endsWith('-'+platform));
      assert.ok(device.trustedDevice&&device.expires===0);
      await worker('devices',{action:'revoke',id:device.id});
      await nativePhone.loadURL(entry);
      await until(()=>native('!document.getElementById("login").hidden'),platform+' revoked binding');
      nativePhone.destroy();nativePhone=null;
    }
    // Save screenshots only after the depicted synthetic grant was consumed/revoked.
    fs.writeFileSync(path.join(data,'desktop-expanded.png'),expandedImage);
    fs.writeFileSync(path.join(data,'desktop-collapsed.png'),(await desktop.capturePage()).toPNG());
    fs.writeFileSync(path.join(data,'phone-signed-in.png'),(await phone.capturePage()).toPNG());
    console.log(JSON.stringify({ok:true,data,checks:['QR PNG decode','collapsed by default','polling and locale preserve expanded grant','refresh revokes previous QR','automatic phone login','fragment removed; no token in query parameters','one-time replay rejected','password fallback','collapse revocation','Android+iOS native UA select durable binding','persistent cookie and gateway restart retain binding','used QR retains existing binding','desktop revocation enforced','no overflow at 390px']}));
  }catch(error){console.error(error);exitCode=1;}
  finally{desktop?.destroy();phone?.destroy();nativePhone?.destroy();if(started)try{await worker('stop');}catch(error){console.error(error);exitCode=1;}app.exit(exitCode);}
});
