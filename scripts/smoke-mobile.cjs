'use strict';
const {app,BrowserWindow}=require('electron');
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const url=new URL(process.argv[2]);
assert.equal(url.hostname,'127.0.0.1');
app.setPath('userData',path.join(process.env.CMB_DATA_DIR,'mobile-test-runtime'));
app.whenReady().then(async()=>{
  const window=new BrowserWindow({show:false,width:390,height:844,webPreferences:{nodeIntegration:false,contextIsolation:true,sandbox:true}});
  try{
    await window.loadURL(url.href);
    await window.webContents.executeJavaScript(fs.readFileSync(path.join(__dirname,'../tests/browser/i18n.browser.js'),'utf8'));
    const result=await window.webContents.executeJavaScript('runI18nTests()');
    assert.equal(result.passed,11);console.log(JSON.stringify(result));app.exit(0);
  }catch(error){console.error(error);app.exit(1);}
});
