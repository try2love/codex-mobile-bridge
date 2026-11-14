'use strict';
const {test,expect}=require('@playwright/test');
const {readFileSync}=require('node:fs');
const path=require('node:path');
const root=path.resolve(__dirname,'../..');
const chinese=/[\u4e00-\u9fff]/;
const now=Date.parse('2030-05-01T00:00:00Z');
const {pairingImage}=require('../../desktop/features/connections/qr.cjs');
const sponsorImage=()=>pairingImage({url:'https://fixture.example.test/purchase'});

test.beforeEach(async({page,context,baseURL,request})=>{
  await context.route('**/*',route=>new URL(route.request().url()).origin===baseURL?route.continue():route.abort());
  expect(await (await request.get('/__fixture_health')).json()).toEqual({fixture:'cmb-browser-regression'});
  await page.goto('/__fixture_health');
  await page.setContent('<!doctype html><html><head></head><body><form id="desktop-settings"><section data-panel="network"></section></form></body></html>');
  await page.evaluate(now=>{
    window.cloudNow=now;Date.now=()=>window.cloudNow;
    window.setInterval=callback=>{window.cloudTick=callback;return 1;};
    window.cloudCalls=[];window.cloudAllCalls=[];window.recordDirectoryCalls=false;window.cloudErrors={};window.cloudPendingAction='';
    window.cloudStatus={configured:true,enabled:true,origin:'https://relay.example.test',account_id:'account-original',login:'original-user',gateway_id:'gateway-original',entitlement:{required:true,active:true,expires_at:'2030-05-02T03:04:05Z',server_time:new Date(now).toISOString()},entitlement_error:'',runtime:{state:'connected'},bindings:[{binding_id:'pinned-original',mobile_public_key:'control-original',preview_public_key:'preview-original',device_name:'Verified phone',allowed:true,remote_status:'active',remote_revision:1,generation:0,safety_code:'12345678 ABCDEF90 12345678 ABCDEF90 12345678 ABCDEF90 12345678 ABCDEF90'}]};
    window.cloudStatus.web_access={allowed:false,revision:0,error:''};
    window.cloudOriginalAccount=JSON.parse(JSON.stringify(window.cloudStatus));
    window.cloudResponses={
      capabilities:{mode:'hosted',email_verification_methods:['code'],registration_flow:'email_code_password'},entitlement:{...window.cloudStatus.entitlement},
      'signup-code':{accepted:true,verification_method:'code',expires_in:600,retry_after:60},forgot:{accepted:true},
      'signup-verify':{verified:true,registration_token:'t'.repeat(43),expires_in:600},register:{registered:true},
      devices:{devices:[]}
    };
    window.bridgeDesktop={cloud:async payload=>{
      window.cloudAllCalls.push({...payload});if(!['devices'].includes(payload.action)||window.recordDirectoryCalls)window.cloudCalls.push({...payload});
      if(window.cloudPendingAction===payload.action)await new Promise(resolve=>window.finishCloud=resolve);
      if(window.cloudErrors[payload.action])throw Error(window.cloudErrors[payload.action]);
      if(payload.action==='web-access'){
        window.cloudStatus.web_access={allowed:payload.allowed,revision:payload.expected_revision+1,error:''};
        return JSON.parse(JSON.stringify(window.cloudStatus));
      }
      if(payload.action==='logout'){
        window.cloudStatus={configured:false,enabled:false,entitlement:null,runtime:{state:'stopped'},bindings:[]};
        return {...window.cloudStatus,logged_out:true,remote_logout_confirmed:true,logout_error:'',credential_clear_error:'',...window.cloudResponses.logout};
      }
      if(payload.action==='access'){
        const item=window.cloudResponses.devices.devices.find(item=>item.binding_id===payload.binding_id)||window.cloudStatus.bindings.find(item=>item.binding_id===payload.binding_id);
        const result=window.cloudResponses.access||{remote_access_confirmed:true,access_error:'',allowed:payload.allowed};
        if(item){
          item.allowed=payload.allowed&&result.remote_access_confirmed===true;item.trusted=true;
          item.generation=(item.generation||0)+1;item.remote_revision=(item.remote_revision||0)+1;
          item.remote_status=payload.allowed?'active':'disabled';item.sync_state=result.remote_access_confirmed?'confirmed':'unconfirmed';
          window.cloudStatus.bindings=window.cloudStatus.bindings.filter(pin=>pin.binding_id!==item.binding_id);
          window.cloudStatus.bindings.push({...item});
        }
        return result;
      }
      const result=payload.action==='status'?window.cloudStatus:(window.cloudResponses[payload.action]||{});
      if(payload.action==='login'&&result.configured)window.cloudStatus=result;
      if(payload.action==='entitlement')window.cloudStatus={...window.cloudStatus,enabled:result.active,entitlement:result,entitlement_error:''};
      if(payload.action==='redeem'&&result.redeemed&&result.cloud)window.cloudStatus=result.cloud;
      return result;
    }};
    window.confirm=message=>{window.cloudConfirmation=message;return false;};
    window.settingsSubmissions=0;document.getElementById('desktop-settings').addEventListener('submit',event=>{event.preventDefault();window.settingsSubmissions++;});
  },now);
  for(const [name,file]of [['style.css','desktop/shell/style.css'],['layout.css','desktop/shell/layout.css']]){
    const url=baseURL+'/__cloud-i18n/'+name;
    await page.route(url,route=>route.fulfill({contentType:'text/css',body:readFileSync(path.join(root,file),'utf8')+'\n#desktop-settings{width:100%;min-width:0;padding:20px}'}));
    await page.evaluate(url=>{const link=document.createElement('link');link.rel='stylesheet';link.href=url;document.head.append(link);},url);
  }
  for(const [name,file]of [['i18n.js','web/shared/i18n.js'],['dialog-dismiss.js','web/shared/dialog-dismiss.js'],['cloud.js','desktop/features/connections/cloud.js']]){
    const url=baseURL+'/__cloud-i18n/'+name;
    await page.route(url,route=>route.fulfill({contentType:'application/javascript',body:readFileSync(path.join(root,file),'utf8')}));
    await page.addScriptTag({url});
  }
  await page.evaluate(()=>{window.cloudInitialServer=document.querySelector('[data-cloud-server]').value;BridgeI18n.setLanguage('zh');window.CloudPanel.update(window.cloudStatus);});
  expect(await page.evaluate(()=>window.cloudCalls.map(call=>call.action))).toEqual([]);
  await page.evaluate(()=>window.cloudCalls=[]);
});

async function language(page,value){await page.evaluate(value=>BridgeI18n.setLanguage(value),value);}
async function openLogin(page){
  if(!await page.locator('#cloud-login-dialog').isVisible()){
    const signedIn=await page.locator('[data-cloud-open-login]').isHidden();
    const previousOrigin=signedIn?await page.evaluate(()=>window.cloudStatus.origin):'';
    if(signedIn){
      // Auth fixtures explicitly log out; signed-in production UI has no login entry.
      await page.locator('[data-cloud-action="logout"]').click();
      await expect(page.locator('[data-cloud-open-login]')).toBeVisible();
      await expect(page.locator('[data-cloud-open-login]')).toBeEnabled();
      await page.evaluate(()=>window.cloudCalls=[]);
    }
    await page.locator('[data-cloud-open-login]').click();
    if(previousOrigin&&previousOrigin!=='https://bridge.waken.cloudns.be:8787'){
      await page.locator('[data-cloud-server]').selectOption('custom');
      await page.locator('[data-cloud-origin]').fill(previousOrigin);
    }
  }
  await expect(page.locator('#cloud-login-dialog')).toBeVisible();
}
async function closeLogin(page){
  if(await page.locator('#cloud-login-dialog').isVisible())await page.locator('[data-cloud-close-login]').click();
  await expect(page.locator('#cloud-login-dialog')).toBeHidden();
}
async function authView(page,view){
  await openLogin(page);
  const current=await page.locator('#cloud-login-dialog').getAttribute('data-view');
  if(current===view)return;
  if(current!=='login')await page.locator('[data-cloud-view="login"]').click();
  if(view!=='login')await page.locator('[data-cloud-view="'+view+'"]').click();
  await expect(page.locator('#cloud-login-dialog')).toHaveAttribute('data-view',view);
}
async function action(page,name){
  if(['signup-code','signup-verify'].includes(name))await authView(page,'register');
  else if(['login','forgot'].includes(name))await authView(page,name);
  else await closeLogin(page);
  await page.locator('[data-cloud-action="'+name+'"]').click();
  await expect(page.locator('[data-cloud-open-login]')).toBeEnabled();
}
async function mockSignIn(page,mode){
  await page.evaluate(mode=>{
    window.cloudResponses.entitlement=mode==='selfhost'?{required:false,active:true,expires_at:null,server_time:new Date(window.cloudNow).toISOString()}:{...window.cloudOriginalAccount.entitlement};
    window.cloudResponses.login={...window.cloudOriginalAccount,entitlement:window.cloudResponses.entitlement};
  },mode);
  await authView(page,'login');
  await page.locator('[data-cloud-login]').fill('original-user');
  await page.locator('[data-cloud-password]').fill('synthetic-test-password');
  await action(page,'login');
  await expect(page.locator('#cloud-login-dialog')).toBeHidden();
}
async function verifySignup(page,login='signup@example.test'){
  await authView(page,'register');
  await page.locator('[data-cloud-login]').fill(login);
  await page.locator('[data-cloud-email-code]').fill('012345');
  await page.locator('[data-cloud-email-code]').press('Enter');
  await expect(page.locator('#cloud-login-dialog')).toHaveAttribute('data-view','password');
}

test('signed-in accounts hide login and relay switches while language changes stay read-only',async({page})=>{
  expect(await page.evaluate(()=>window.cloudInitialServer)).toBe('official');
  await expect(page.locator('#cloud-login-dialog')).toBeHidden();
  await expect(page.locator('#cloud-account input[data-cloud-login]')).toHaveCount(0);
  await expect(page.locator('[data-cloud-open-login]')).toHaveText(chinese);
  await expect(page.locator('[data-cloud-open-login]')).toBeHidden();
  await expect(page.locator('[data-cloud-action="enabled"]')).toHaveCount(0);
  await language(page,'en');
  await expect(page.locator('[data-cloud-identity]')).toHaveText('https://relay.example.test / original-user / Gateway ID: gateway-original');
  await expect(page.locator('[data-cloud-devices] [data-cloud-allow="pinned-original"]')).toBeChecked();
  await language(page,'zh');
  expect(await page.evaluate(()=>window.cloudCalls)).toEqual([]);
  await openLogin(page);
  await expect(page.locator('[data-cloud-server] option').first()).toHaveAttribute('value','official');
  await expect(page.locator('[data-cloud-server] option').first()).toHaveText(chinese);
  await expect(page.locator('[data-cloud-server]')).toHaveValue('custom');
  await expect(page.locator('[data-cloud-origin]')).toHaveValue('https://relay.example.test');
  await page.evaluate(()=>window.CloudPanel.update(window.cloudStatus));
  await language(page,'en');
  await expect(page.locator('[data-cloud-open-login]')).toHaveText('Sign In to Cloud');
  await expect(page.locator('[data-cloud-server] option').first()).toHaveText('Official Cloud');
  await expect(page.locator('[data-cloud-origin]')).toHaveValue('https://relay.example.test');
  expect(await page.evaluate(()=>window.cloudCalls)).toEqual([]);
});

test('logout restores sign-in and clears account details without another request or settings submission',async({page})=>{
  await language(page,'en');
  await page.locator('[data-cloud-action="logout"]').click();
  await expect(page.locator('[data-cloud-open-login]')).toBeVisible();
  await expect(page.locator('[data-cloud-account]')).toBeHidden();
  await expect(page.locator('[data-cloud-identity]')).toHaveText('');
  await expect(page.locator('[data-cloud-entitlement]')).toHaveText('');
  await expect(page.locator('[data-cloud-result]')).toHaveText('Signed out of the cloud account.');
  await language(page,'zh');
  await expect(page.locator('[data-cloud-result]')).toHaveText('\u5df2\u9000\u51fa\u4e91\u8d26\u53f7\u3002');
  await page.evaluate(async()=>{window.cloudNow+=60000;await window.cloudTick();});
  expect(await page.evaluate(()=>window.cloudCalls.map(call=>call.action))).toEqual(['logout']);
  expect(await page.evaluate(()=>window.settingsSubmissions)).toBe(0);
});

for(const code of ['login_required','authentication_required','network_or_certificate_error']){
  test('local logout remains successful when remote revocation reports '+code,async({page})=>{
    await page.evaluate(code=>window.cloudResponses.logout={remote_logout_confirmed:false,logout_error:code},code);
    await language(page,'en');
    await page.locator('[data-cloud-action="logout"]').click();
    await expect(page.locator('[data-cloud-open-login]')).toBeVisible();
    await expect(page.locator('[data-cloud-account]')).toBeHidden();
    await expect(page.locator('[data-cloud-result]')).toHaveText('Signed out locally. Cloud session revocation could not be confirmed; direct connections are unchanged.');
    await language(page,'zh');
    await expect(page.locator('[data-cloud-result]')).toContainText('\u5df2\u5728\u672c\u673a\u9000\u51fa');
    await page.evaluate(async()=>{window.cloudNow+=60000;await window.cloudTick();});
    expect(await page.evaluate(()=>window.cloudCalls.map(call=>call.action))).toEqual(['logout']);
  });
}

test('legacy logout errors are reconciled only after a signed-out status is confirmed',async({page})=>{
  await page.evaluate(()=>{
    const invoke=window.bridgeDesktop.cloud;
    window.bridgeDesktop.cloud=async payload=>{
      const result=await invoke(payload);
      if(payload.action==='logout')throw Error('Cloud service: login_required');
      return result;
    };
  });
  await language(page,'en');
  await page.locator('[data-cloud-action="logout"]').click();
  await expect(page.locator('[data-cloud-open-login]')).toBeVisible();
  await expect(page.locator('[data-cloud-result]')).toHaveText('Signed out locally. Cloud session revocation could not be confirmed; direct connections are unchanged.');
  expect(await page.evaluate(()=>window.cloudCalls.map(call=>call.action))).toEqual(['logout','status']);
});

test('a signed-out snapshot arriving before the logout response does not hide completion feedback',async({page})=>{
  await page.evaluate(()=>window.cloudPendingAction='logout');
  await language(page,'en');
  await page.locator('[data-cloud-action="logout"]').click();
  await expect.poll(()=>page.evaluate(()=>typeof window.finishCloud)).toBe('function');
  await page.evaluate(()=>{
    window.cloudStatus={configured:false,enabled:false,entitlement:null,runtime:{state:'stopped'},bindings:[]};
    window.CloudPanel.update(window.cloudStatus);window.finishCloud();
  });
  await expect(page.locator('[data-cloud-open-login]')).toBeVisible();
  await expect(page.locator('[data-cloud-result]')).toHaveText('Signed out of the cloud account.');
  expect(await page.evaluate(()=>window.cloudCalls.map(call=>call.action))).toEqual(['logout']);
});

test('a delayed entitlement response after logout cannot restore account details or start more cloud work',async({page})=>{
  await page.evaluate(()=>{window.cloudPendingAction='entitlement';document.querySelector('[data-cloud-action="entitlement"]').click();});
  await expect.poll(()=>page.evaluate(()=>typeof window.finishCloud)).toBe('function');
  await language(page,'en');
  await page.evaluate(()=>{window.cloudStatus={configured:false,enabled:false};window.CloudPanel.update(window.cloudStatus);});
  await expect(page.locator('[data-cloud-open-login]')).toBeVisible();
  await page.evaluate(()=>window.finishCloud());
  await expect(page.locator('[data-cloud-entitlement]')).toHaveText('');
  await expect(page.locator('[data-cloud-result]')).toHaveText('');
  await page.evaluate(async()=>{window.cloudNow+=60000;await window.cloudTick();});
  expect(await page.evaluate(()=>window.cloudCalls.map(call=>call.action))).toEqual(['entitlement']);
});

test('official cloud selection changes only the draft and clears a different server password',async({page})=>{
  await openLogin(page);
  await page.locator('[data-cloud-password]').fill('private-custom-server-only');
  await page.locator('[data-cloud-server]').selectOption('official');
  await expect(page.locator('[data-cloud-official-address]')).toHaveCount(0);
  await expect(page.locator('#cloud-login-dialog')).not.toContainText('bridge.waken.cloudns.be');
  await expect(page.locator('[data-cloud-custom-origin]')).toBeHidden();
  await expect(page.locator('[data-cloud-password]')).toHaveValue('');
  await expect(page.locator('[data-cloud-account]')).toBeHidden();
  expect(await page.evaluate(()=>window.cloudCalls)).toEqual([]);
  await page.evaluate(()=>window.CloudPanel.update(window.cloudStatus));
  await language(page,'en');
  await expect(page.locator('[data-cloud-server]')).toHaveValue('official');
  await page.evaluate(()=>window.cloudErrors.login='Cloud service: invalid_credentials');
  await page.locator('[data-cloud-password]').fill('synthetic-test-password');
  await action(page,'login');
  expect(await page.evaluate(()=>window.cloudCalls)).toEqual([{action:'login',origin:'https://bridge.waken.cloudns.be:8787',login:'original-user',password:'synthetic-test-password'}]);
  expect(await page.evaluate(()=>window.cloudStatus.configured)).toBe(false);
});

test('manually edited or cleared cloud addresses survive status refresh and language changes',async({page})=>{
  await openLogin(page);
  await page.locator('[data-cloud-origin]').fill('https://selfhost.example.test:9443');
  await page.evaluate(()=>window.CloudPanel.update(window.cloudStatus));
  await language(page,'en');
  await expect(page.locator('[data-cloud-origin]')).toHaveValue('https://selfhost.example.test:9443');
  await page.locator('[data-cloud-origin]').fill('');
  await page.evaluate(()=>window.CloudPanel.update(window.cloudStatus));
  await language(page,'zh');
  await expect(page.locator('[data-cloud-origin]')).toHaveValue('');
  expect(await page.evaluate(()=>window.cloudCalls)).toEqual([]);
});

test('cloud login drafts switch languages without losing inputs or making requests',async({page})=>{
  await openLogin(page);
  await expect(page.locator('#cloud-account h2')).toHaveText('\u4e91\u670d\u52a1');
  await expect(page.locator('[data-cloud-action="login"]')).toHaveText(chinese);
  await expect(page.locator('[data-cloud-qr]')).toHaveCount(0);
  const drafts={origin:'https://other.example.test',login:'draft-user',password:'private-test-only',code:'test-code'};
  for(const [name,value]of Object.entries(drafts)){
    const input=page.locator('[data-cloud-'+name+']');
    if(name==='code')await input.evaluate((node,value)=>node.value=value,value);
    else await input.fill(value);
  }
  await language(page,'en');
  await expect(page.locator('#cloud-account h2')).toHaveText('Cloud Relay');
  await expect(page.locator('[data-cloud-action="enabled"]')).toHaveCount(0);
  await expect(page.locator('[data-cloud-state]')).toHaveText('Relay stopped');
  await expect(page.locator('[data-cloud-qr]')).toHaveCount(0);
  await expect(page.locator('[data-cloud-account]')).toBeHidden();
  await language(page,'zh');
  await expect(page.locator('#cloud-account h2')).toHaveText('\u4e91\u670d\u52a1');
  for(const [name,value]of Object.entries(drafts))await expect(page.locator('[data-cloud-'+name+']')).toHaveValue(value);
  expect(await page.evaluate(()=>window.cloudCalls)).toEqual([]);
  await closeLogin(page);
  expect(await page.evaluate(()=>window.cloudCalls)).toEqual([]);
});

test('sign-in uses backend service mode and cached entitlements use the selected date locale',async({page})=>{
  await expect(page.locator('[data-cloud-redeem]')).toBeVisible();
  await mockSignIn(page,'hosted');
  expect(await page.evaluate(()=>window.cloudCalls.filter(call=>call.action==='capabilities'))).toEqual([]);
  await language(page,'en');
  await expect(page.locator('[data-cloud-redeem]')).toBeVisible();
  const redemptionGap=await page.locator('[data-cloud-redeem]').evaluate(node=>
    node.querySelector('[data-cloud-action="redeem"]').getBoundingClientRect().top-node.querySelector('[data-cloud-code]').getBoundingClientRect().bottom);
  expect(redemptionGap).toBeGreaterThanOrEqual(12);
  await action(page,'entitlement');
  await expect(page.locator('[data-cloud-result]')).toHaveText('Cloud service status refreshed.');
  const englishDate=await page.evaluate(()=>new Date(window.cloudResponses.entitlement.expires_at).toLocaleString('en-US'));
  await expect(page.locator('[data-cloud-entitlement]')).toHaveText('Active until '+englishDate);
  const calls=await page.evaluate(()=>window.cloudCalls.length);
  await language(page,'zh');
  await expect(page.locator('[data-cloud-result]')).toHaveText('\u670d\u52a1\u72b6\u6001\u5df2\u5237\u65b0\u3002');
  const chineseDate=await page.evaluate(()=>new Date(window.cloudResponses.entitlement.expires_at).toLocaleString('zh-CN'));
  await expect(page.locator('[data-cloud-entitlement]')).toHaveText('\u670d\u52a1\u6709\u6548\u671f\u81f3 '+chineseDate);
  expect(await page.evaluate(()=>window.cloudCalls.length)).toBe(calls);
  await mockSignIn(page,'selfhost');await action(page,'entitlement');
  await language(page,'en');
  await expect(page.locator('[data-cloud-entitlement]')).toHaveText('Active (selfhost)');
  await expect(page.locator('[data-cloud-redeem]')).toBeHidden();
  await page.evaluate(()=>window.cloudResponses.entitlement={...window.cloudResponses.entitlement,active:false});
  await action(page,'entitlement');
  await language(page,'zh');
  await expect(page.locator('[data-cloud-entitlement]')).toContainText(chinese);
});

test('cloud errors translate while preserving diagnostic codes and unknown server values',async({page})=>{
  await page.evaluate(()=>{window.cloudStatus.runtime={state:'unavailable',code:'entitlement_required'};window.CloudPanel.update(window.cloudStatus);});
  await expect(page.locator('[data-cloud-state]')).toContainText(chinese);
  await expect(page.locator('[data-cloud-state]')).toContainText('(entitlement_required)');
  await openLogin(page);await page.locator('[data-cloud-login]').fill('recovery@example.test');
  await page.evaluate(()=>window.cloudErrors.forgot="Error invoking remote method 'bridge:cloud': Error: Cloud service: login_required");
  await action(page,'forgot');
  await expect(page.locator('[data-cloud-result]')).toContainText(chinese);
  await expect(page.locator('[data-cloud-result]')).toContainText('(login_required)');
  await language(page,'en');
  await expect(page.locator('[data-cloud-result]')).toHaveText('Cloud service: Sign in to the cloud account first. (login_required)');
  await page.evaluate(()=>window.cloudErrors.forgot='An exact HTTPS origin is required');
  await action(page,'forgot');
  await language(page,'zh');
  await expect(page.locator('[data-cloud-result]')).toContainText(chinese);
  await page.evaluate(()=>window.cloudErrors.forgot='Invalid cloud field: origin');
  await action(page,'forgot');
  await expect(page.locator('[data-cloud-result]')).toContainText(chinese);
  await expect(page.locator('[data-cloud-result]')).toContainText('(origin)');
  await page.evaluate(()=>{window.cloudErrors.forgot='Cloud service: future_error';window.cloudStatus.runtime={state:'future-state',code:'future-code'};window.CloudPanel.update(window.cloudStatus);});
  await action(page,'forgot');
  await expect(page.locator('[data-cloud-state]')).toHaveText('future-state / future-code');
  await expect(page.locator('[data-cloud-result]')).toHaveText('\u4e91\u670d\u52a1\uff1afuture_error');
});

test('account phones appear without a popup and their access switch is reversible',async({page},testInfo)=>{
  await page.evaluate(()=>{window.recordDirectoryCalls=true;window.cloudStatus.bindings=[];window.CloudPanel.update(window.cloudStatus);window.cloudResponses.devices={devices:[{binding_id:'request-original',device_name:'Test phone',mobile_public_key:'candidate-control',preview_public_key:'candidate-preview',safety_code:'12345678 ABCDEF90 12345678 ABCDEF90 12345678 ABCDEF90 12345678 ABCDEF90',proof_verified:true,allowed:false,remote_status:'pending',remote_revision:0,generation:0,online:true}]};return window.cloudTick();});
  await expect(page.locator('#cloud-pairing-dialog')).toHaveCount(0);
  const devices=page.locator('[data-cloud-devices]');
  await expect(devices).toContainText('Test phone');
  const toggle=devices.locator('[data-cloud-allow]');
  await expect(toggle).not.toBeChecked();await expect(toggle).toBeEnabled();
  await expect(page.locator('[data-cloud-qr]')).toHaveCount(0);
  await expect(page.locator('[data-cloud-action="pairing"]')).toHaveCount(0);
  await language(page,'en');await expect(devices).toContainText('12345678 ABCDEF90 12345678 ABCDEF90 12345678 ABCDEF90 12345678 ABCDEF90');
  const screenshot=testInfo.outputPath('desktop-account-devices.png');await page.locator('#cloud-account').screenshot({path:screenshot});await testInfo.attach('desktop-account-devices',{path:screenshot,contentType:'image/png'});
  await page.evaluate(()=>{window.cloudNow+=365*24*60*60*1000;return window.cloudTick();});
  await expect(page.locator('[data-cloud-devices]')).toContainText('Test phone');
  await expect(page.locator('[data-cloud-devices] [data-cloud-allow]')).not.toBeChecked();
  expect(await page.evaluate(()=>window.cloudCalls.some(call=>['approve','reject','access'].includes(call.action)))).toBe(false);
  await page.evaluate(()=>window.confirm=()=>true);
  await page.locator('[data-cloud-devices] [data-cloud-allow]').click();
  await expect(page.locator('[data-cloud-devices] [data-cloud-allow="request-original"]')).toBeChecked();
  expect(await page.evaluate(()=>window.cloudCalls.filter(call=>call.action==='access'))).toEqual([{action:'access',binding_id:'request-original',allowed:true,expected_revision:0,safety_code:'12345678 ABCDEF90 12345678 ABCDEF90 12345678 ABCDEF90 12345678 ABCDEF90'}]);
  await toggle.click();await expect(toggle).not.toBeChecked();
  await expect(devices).toContainText('Test phone');
  await toggle.click();await expect(toggle).toBeChecked();
  expect(await page.evaluate(()=>window.cloudCalls.filter(call=>call.action==='access').slice(1))).toEqual([
    {action:'access',binding_id:'request-original',allowed:false,expected_revision:1},
    {action:'access',binding_id:'request-original',allowed:true,expected_revision:2}
  ]);
  expect(await page.evaluate(()=>window.settingsSubmissions)).toBe(0);
});

test('an unverified discovered phone cannot connect and has no rejection or delete button',async({page})=>{
  await page.evaluate(()=>{window.cloudStatus.bindings=[];window.CloudPanel.update(window.cloudStatus);window.cloudResponses.devices={devices:[{binding_id:'unverified',device_name:'Unverified phone',mobile_public_key:'unverified-control',proof_verified:false,allowed:false,remote_status:'pending',remote_revision:0,generation:0}]};return window.cloudTick();});
  await expect(page.locator('[data-cloud-devices] [data-cloud-allow]')).toBeDisabled();
  await expect(page.locator('#cloud-pairing-dialog')).toHaveCount(0);
  await expect(page.locator('[data-cloud-devices] button')).toHaveCount(0);
  expect(await page.evaluate(()=>window.cloudCalls.filter(call=>['approve','reject','access'].includes(call.action)))).toEqual([]);
});

test('switching language keeps pending operations disabled and never retries failed writes',async({page})=>{
  await page.evaluate(()=>window.cloudPendingAction='redeem');
  await page.locator('[data-cloud-code]').fill('TESTONLY234567AB');
  await page.locator('[data-cloud-action="redeem"]').click();
  await expect(page.locator('[data-cloud-action="login"]')).toBeDisabled();
  await language(page,'en');
  await expect(page.locator('[data-cloud-result]')).toHaveText('Working...');
  await expect(page.locator('[data-cloud-action="redeem"]')).toBeDisabled();
  await expect(page.locator('[data-cloud-server]')).toBeDisabled();
  await expect(page.locator('[data-cloud-open-login]')).toBeDisabled();
  await expect(page.locator('[data-cloud-devices] [data-cloud-allow]')).toBeDisabled();
  expect(await page.evaluate(()=>window.cloudCalls)).toEqual([{action:'redeem',code:'TESTONLY234567AB'}]);
  await page.evaluate(()=>{window.cloudErrors.redeem='Cloud service: network_or_certificate_error';window.finishCloud();});
  await expect(page.locator('[data-cloud-action="redeem"]')).toBeEnabled();
  await expect(page.locator('[data-cloud-result]')).toContainText('(network_or_certificate_error)');
  await language(page,'zh');
  await expect(page.locator('[data-cloud-result]')).toContainText(chinese);
  expect(await page.evaluate(()=>window.cloudCalls)).toEqual([{action:'redeem',code:'TESTONLY234567AB'}]);
});

test('confirmed redemption updates service dates and translates feedback without repeating the code',async({page})=>{
  await page.evaluate(()=>{
    const entitlement={...window.cloudStatus.entitlement,expires_at:'2030-06-01T00:00:00Z'};
    window.cloudResponses.redeem={redeemed:true,entitlement,refresh_error:'',cloud:{...window.cloudStatus,entitlement}};
  });
  await page.locator('[data-cloud-code]').fill('TESTONLY234567AB');await action(page,'redeem');
  await expect(page.locator('[data-cloud-result]')).toContainText(chinese);
  await expect(page.locator('[data-cloud-code]')).toHaveValue('');
  const calls=await page.evaluate(()=>window.cloudCalls);
  await language(page,'en');
  await expect(page.locator('[data-cloud-result]')).toHaveText('Service days redeemed. Service status refreshed.');
  const date=await page.evaluate(()=>new Date('2030-06-01T00:00:00Z').toLocaleString('en-US'));
  await expect(page.locator('[data-cloud-entitlement]')).toHaveText('Active until '+date);
  await language(page,'zh');expect(await page.evaluate(()=>window.cloudCalls)).toEqual(calls);
  expect(calls.map(call=>call.action)).toEqual(['redeem','status']);
});

test('confirmed redemption with a status refresh failure warns against redeeming again in either language',async({page})=>{
  await page.evaluate(()=>window.cloudResponses.redeem={redeemed:true,entitlement:null,refresh_error:'network_or_certificate_error',cloud:{...window.cloudStatus,enabled:false,entitlement:null,entitlement_error:'network_or_certificate_error'}});
  await page.locator('[data-cloud-code]').fill('TESTONLY234567AB');await action(page,'redeem');
  await expect(page.locator('[data-cloud-result]')).toContainText(chinese);
  await language(page,'en');
  await expect(page.locator('[data-cloud-result]')).toHaveText('Service days redeemed, but service status could not be refreshed. Refresh status; do not redeem again.');
  await page.evaluate(()=>{window.cloudNow+=5000;return window.cloudTick();});
  expect(await page.evaluate(()=>window.cloudCalls.map(call=>call.action))).toEqual(['redeem','status']);
  expect(await page.evaluate(()=>window.cloudCalls.filter(call=>call.action==='redeem').length)).toBe(1);
});

test('sponsorship appears only for a signed-in official cloud account',async({page},testInfo)=>{
  const sponsor=page.locator('[data-cloud-sponsor]');
  await expect(sponsor).toBeHidden();
  await page.evaluate(()=>{window.cloudStatus={configured:false,enabled:false};window.CloudPanel.update(window.cloudStatus);});
  await expect(sponsor).toBeHidden();
  await page.screenshot({path:testInfo.outputPath('cloud-sponsor-signed-out.png')});
  await openLogin(page);
  await page.locator('[data-cloud-server]').selectOption('custom');
  await expect(sponsor).toBeHidden();
  await page.locator('[data-cloud-origin]').fill('https://bridge.waken.cloudns.be:8787');
  await expect(sponsor).toBeHidden();
  await language(page,'en');
  await page.evaluate(()=>window.CloudPanel.update(window.cloudStatus));
  await expect(sponsor).toBeHidden();
  await page.locator('[data-cloud-server]').selectOption('official');
  await expect(sponsor).toBeHidden();
  await page.locator('[data-cloud-server]').selectOption('custom');
  await closeLogin(page);
  for(const entitlement of [null,{required:true,active:false,expires_at:null},{required:true,active:true,expires_at:'2031-01-01T00:00:00Z'},{required:true,active:false,expires_at:'2029-01-01T00:00:00Z'}]){
    await page.evaluate(entitlement=>{
      window.cloudStatus={...window.cloudOriginalAccount,origin:'https://bridge.waken.cloudns.be:8787',entitlement};
      window.CloudPanel.update(window.cloudStatus);
    },entitlement);
    await expect(sponsor).toBeVisible();
  }
  await page.evaluate(()=>window.CloudPanel.update(window.cloudOriginalAccount));
  await expect(sponsor).toBeHidden();
  expect(await page.evaluate(()=>window.cloudAllCalls)).toEqual([]);
  await page.evaluate(()=>{
    window.cloudStatus={...window.cloudOriginalAccount,origin:'https://bridge.waken.cloudns.be:8787'};
    window.CloudPanel.update(window.cloudStatus);
  });
  await expect(sponsor).toBeVisible();
  await language(page,'zh');
  await page.screenshot({path:testInfo.outputPath('cloud-sponsor-signed-in.png')});
  await action(page,'logout');
  await expect(sponsor).toBeHidden();
  await language(page,'en');
  await page.evaluate(()=>window.CloudPanel.update(window.cloudStatus));
  await expect(sponsor).toBeHidden();
  expect(await page.evaluate(()=>window.cloudAllCalls)).toEqual([{action:'logout'}]);
});

test('sponsor purchase opens a QR dialog, preserves drafts and fits both UI languages',async({page},testInfo)=>{
  const qr=await sponsorImage();
  await page.evaluate(qr=>{
    window.cloudOpened=[];
    window.bridgeDesktop.open=async target=>window.cloudOpened.push(target);
    window.cloudResponses.sponsorship=qr;
    window.cloudStatus={...window.cloudOriginalAccount,origin:'https://bridge.waken.cloudns.be:8787'};
    window.CloudPanel.update(window.cloudStatus);
  },qr);
  const sponsor=page.locator('[data-cloud-sponsor]'),button=page.locator('[data-cloud-open-sponsor]');
  const modal=page.locator('#cloud-sponsor-dialog'),image=page.locator('[data-cloud-sponsor-qr-image]');
  await page.locator('[data-cloud-code]').fill('UNSENT-TEST-CODE');
  await expect(sponsor.getByRole('heading')).toHaveText('赞助支持');
  await expect(button).toHaveText('购买官方云服务 30 天激活码（闲鱼）');
  await button.click();
  await expect(modal).toBeVisible();await expect(image).toBeVisible();await expect(image).toHaveAttribute('src',qr.image);
  await expect(page.locator('[data-cloud-show-sponsor-qr]')).toHaveCount(0);
  expect(await modal.evaluate(node=>node.closest('form'))).toBeNull();
  for(const locale of ['zh','en']){
    await language(page,locale);
    const box=await modal.boundingBox(),viewport=page.viewportSize();
    expect(box.x).toBeGreaterThanOrEqual(0);expect(box.x+box.width).toBeLessThanOrEqual(viewport.width);
    expect(box.y).toBeGreaterThanOrEqual(0);expect(box.y+box.height).toBeLessThanOrEqual(viewport.height);
    expect(await modal.evaluate(node=>node.scrollWidth<=node.clientWidth)).toBe(true);
    const screenshot=testInfo.outputPath('cloud-sponsor-'+locale+'.png');
    await page.screenshot({path:screenshot});
    await testInfo.attach('cloud-sponsor-'+locale,{path:screenshot,contentType:'image/png'});
  }
  await expect(button).toHaveText('Buy a 30-day Official Cloud activation code (Xianyu)');
  await page.keyboard.press('Escape');await expect(modal).toBeHidden();await expect(button).toBeFocused();
  await button.click();await expect(modal).toBeVisible();await page.mouse.click(4,4);await expect(modal).toBeHidden();
  await button.click();await page.locator('[data-cloud-close-sponsor]').click();await expect(modal).toBeHidden();
  await page.evaluate(()=>window.CloudPanel.update(window.cloudStatus));
  await expect(page.locator('[data-cloud-code]')).toHaveValue('UNSENT-TEST-CODE');
  await expect(page.locator('[data-cloud-identity]')).toContainText('original-user');
  expect(await page.evaluate(()=>({opened:window.cloudOpened,cloud:window.cloudAllCalls,submissions:window.settingsSubmissions}))).toEqual({opened:[],cloud:Array.from({length:3},()=>({action:'sponsorship',origin:'https://bridge.waken.cloudns.be:8787'})),submissions:0});
});

test('sponsor QR retries on reopening and closes on a custom account or logout',async({page})=>{
  const qr=await sponsorImage();
  await page.evaluate(qr=>{
    window.cloudErrors.sponsorship='synthetic QR failure';window.cloudResponses.sponsorship=qr;
    window.cloudStatus={...window.cloudOriginalAccount,origin:'https://bridge.waken.cloudns.be:8787'};window.CloudPanel.update(window.cloudStatus);
  },qr);
  const button=page.locator('[data-cloud-open-sponsor]'),image=page.locator('[data-cloud-sponsor-qr-image]'),modal=page.locator('#cloud-sponsor-dialog');
  await button.click();await expect(modal).toBeVisible();await expect(image).toBeHidden();
  await expect(page.locator('[data-cloud-sponsor-qr-status]')).toHaveText('无法加载二维码，请关闭后重试。');
  await language(page,'en');await expect(page.locator('[data-cloud-sponsor-qr-status]')).toHaveText('Could not load the QR code. Close it and try again.');
  expect(await page.evaluate(()=>window.cloudAllCalls.length)).toBe(1);
  await page.evaluate(()=>delete window.cloudErrors.sponsorship);
  await page.locator('[data-cloud-close-sponsor]').click();await button.click();await expect(image).toBeVisible();
  expect(await page.evaluate(()=>window.cloudAllCalls.length)).toBe(2);
  await page.evaluate(()=>window.CloudPanel.update(window.cloudOriginalAccount));await expect(modal).toBeHidden();
  expect(await page.evaluate(()=>({cloud:window.cloudAllCalls,settings:window.settingsSubmissions}))).toEqual({cloud:Array.from({length:2},()=>({action:'sponsorship',origin:'https://bridge.waken.cloudns.be:8787'})),settings:0});
  await page.evaluate(()=>{
    window.CloudPanel.update(window.cloudStatus);
    window.cloudPendingAction='sponsorship';
  });
  await button.click();await expect(modal).toBeVisible();
  await expect(page.locator('[data-cloud-sponsor-qr-status]')).toHaveText('Loading QR code...');
  await page.evaluate(()=>{
    window.cloudStatus={configured:false,enabled:false};
    window.CloudPanel.update(window.cloudStatus);
  });
  await expect(modal).toBeHidden();await expect(page.locator('[data-cloud-sponsor]')).toBeHidden();
  await page.evaluate(()=>window.finishCloud());
  await expect(page.locator('[data-cloud-sponsor-qr-status]')).toHaveText('');
  await expect(modal).toBeHidden();await expect(page.locator('[data-cloud-sponsor]')).toBeHidden();
});

test('enabled web cloud access displays the advertised origin without the redemption separator',async({page},testInfo)=>{
  await page.evaluate(()=>window.cloudResponses.capabilities.web_client={enabled:true,origin:'https://web.example.test:18787'});
  await page.locator('[data-cloud-web-allow]').check();
  const address=page.locator('[data-cloud-web-address]');
  await expect(address).toBeVisible();await expect(address).toHaveValue('https://web.example.test:18787');
  await expect(address).toHaveAttribute('readonly','');
  await address.click();expect(await address.evaluate(n=>n.selectionEnd-n.selectionStart)).toBe('https://web.example.test:18787'.length);
  await language(page,'en');await page.evaluate(()=>window.CloudPanel.update(window.cloudStatus));
  expect(await page.evaluate(()=>window.cloudAllCalls.filter(c=>c.action==='capabilities'))).toEqual([{action:'capabilities',origin:'https://relay.example.test'}]);
  expect(await page.locator('.cloud-web-access').evaluate(n=>getComputedStyle(n).borderTopWidth)).toBe('0px');
  const gap=await page.evaluate(()=>document.querySelector('.cloud-web-access').getBoundingClientRect().top-document.querySelector('[data-cloud-redeem]').getBoundingClientRect().bottom);
  expect(gap).toBeGreaterThanOrEqual(24);
  expect(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth)).toBe(false);
  await page.screenshot({path:testInfo.outputPath('web-cloud-address.png')});
  await page.locator('[data-cloud-web-allow]').uncheck();await expect(address).toBeHidden();await expect(address).toHaveValue('');
});

test('new accounts use the official server and the login dialog fits wide and narrow screens',async({page},testInfo)=>{
  await page.evaluate(()=>{window.cloudStatus={configured:false,enabled:false};window.CloudPanel.update(window.cloudStatus);});
  await expect(page.locator('[data-cloud-account]')).toBeHidden();
  await openLogin(page);
  await expect(page.locator('[data-cloud-server]')).toHaveValue('official');
  await expect(page.locator('[data-cloud-origin]')).toBeHidden();
  await expect(page.locator('[data-cloud-origin]')).toBeDisabled();
  await expect(page.locator('[data-cloud-official-address]')).toHaveCount(0);
  await expect(page.locator('#cloud-login-dialog')).not.toContainText('bridge.waken.cloudns.be');
  await expect(page.locator('[data-cloud-server]')).toBeFocused();
  await language(page,'en');
  await expect(page.getByRole('dialog')).toHaveAccessibleName('Sign In to Cloud');
  const box=await page.locator('#cloud-login-dialog').boundingBox(),viewport=page.viewportSize();
  expect(box.x).toBeGreaterThanOrEqual(0);expect(box.y).toBeGreaterThanOrEqual(0);
  expect(box.width).toBeLessThanOrEqual(viewport.width);expect(box.height).toBeLessThanOrEqual(viewport.height);
  expect(await page.locator('#cloud-login-dialog').evaluate(node=>node.scrollWidth<=node.clientWidth)).toBe(true);
  expect(await page.evaluate(()=>document.querySelector('#cloud-login-dialog').closest('form'))).toBeNull();
  expect(await page.evaluate(()=>window.cloudCalls)).toEqual([]);
  const screenshot=testInfo.outputPath('cloud-login-dialog.png');
  await page.screenshot({path:screenshot});
  await testInfo.attach('cloud-login-dialog',{path:screenshot,contentType:'image/png'});
});

test('closing or dismissing login clears only the password and never submits settings',async({page})=>{
  await openLogin(page);
  await page.locator('[data-cloud-password]').fill('test-password-to-clear');
  await page.locator('[data-cloud-login]').fill('draft-user-to-keep');
  await page.keyboard.press('Escape');
  await expect(page.locator('#cloud-login-dialog')).toBeHidden();
  await expect(page.locator('[data-cloud-password]')).toHaveValue('');
  await expect(page.locator('[data-cloud-open-login]')).toBeFocused();
  await openLogin(page);
  await expect(page.locator('[data-cloud-login]')).toHaveValue('draft-user-to-keep');
  await expect(page.locator('[data-cloud-origin]')).toHaveValue('https://relay.example.test');
  await page.locator('[data-cloud-password]').fill('another-test-password');
  await page.mouse.click(4,4);
  await expect(page.locator('#cloud-login-dialog')).toBeHidden();
  await expect(page.locator('[data-cloud-password]')).toHaveValue('');
  expect(await page.evaluate(()=>window.cloudCalls)).toEqual([]);
  expect(await page.evaluate(()=>window.settingsSubmissions)).toBe(0);
});

test('login submits once to the selected server and closes only after the account is confirmed',async({page})=>{
  await page.evaluate(()=>{
    window.cloudStatus={configured:false,enabled:false};window.CloudPanel.update(window.cloudStatus);
    window.cloudResponses.login={configured:true,enabled:false,origin:'https://bridge.waken.cloudns.be:8787',account_id:'new-account',login:'new-user',gateway_id:'new-gateway',entitlement:{required:true,active:false,expires_at:null,server_time:new Date(window.cloudNow).toISOString()},runtime:{state:'disabled'},bindings:[]};
  });
  await openLogin(page);
  await page.locator('[data-cloud-login]').fill('new-user');
  await page.locator('[data-cloud-password]').fill('synthetic-test-password');
  await page.locator('[data-cloud-password]').press('Enter');
  await expect(page.locator('#cloud-login-dialog')).toBeHidden();
  await expect(page.locator('[data-cloud-identity]')).toContainText('new-user');
  await expect(page.locator('[data-cloud-identity]')).toContainText(chinese);
  await expect(page.locator('#cloud-account')).not.toContainText('bridge.waken.cloudns.be');
  await language(page,'en');
  await expect(page.locator('[data-cloud-identity]')).toHaveText('Official Cloud / new-user / Gateway ID: new-gateway');
  await language(page,'zh');
  await expect(page.locator('[data-cloud-result]')).toContainText(chinese);
  await expect(page.locator('[data-cloud-password]')).toHaveValue('');
  await expect(page.locator('[data-cloud-action="pairing"]')).toHaveCount(0);
  expect(await page.evaluate(()=>window.cloudCalls)).toEqual([
    {action:'login',origin:'https://bridge.waken.cloudns.be:8787',login:'new-user',password:'synthetic-test-password'},
    {action:'status'}
  ]);
  expect(await page.evaluate(()=>window.settingsSubmissions)).toBe(0);
  expect(await page.evaluate(()=>window.cloudStatus.enabled)).toBe(false);
});

test('pending login blocks dismissal and server changes without replaying an unknown write',async({page})=>{
  await page.evaluate(()=>window.cloudPendingAction='login');
  await openLogin(page);
  await page.locator('[data-cloud-login]').fill('test-user');
  await page.locator('[data-cloud-password]').fill('synthetic-test-password');
  await page.locator('[data-cloud-action="login"]').click();
  await expect(page.locator('[data-cloud-server]')).toBeDisabled();
  await expect(page.locator('[data-cloud-origin]')).toBeDisabled();
  await expect(page.locator('[data-cloud-close-login]')).toBeDisabled();
  for(const view of ['forgot','register'])await expect(page.locator('[data-cloud-view="'+view+'"]')).toBeDisabled();
  await page.keyboard.press('Escape');
  await page.mouse.click(4,4);
  await expect(page.locator('#cloud-login-dialog')).toBeVisible();
  await language(page,'en');
  await expect(page.locator('[data-cloud-login-result]')).toHaveText('Working...');
  expect(await page.evaluate(()=>window.cloudCalls.length)).toBe(1);
  await page.evaluate(()=>{window.cloudErrors.login='Cloud service: network_or_certificate_error';window.finishCloud();});
  await expect(page.locator('[data-cloud-action="login"]')).toBeEnabled();
  await expect(page.locator('#cloud-login-dialog')).toBeVisible();
  await expect(page.locator('[data-cloud-login-result]')).toContainText('(network_or_certificate_error)');
  await expect(page.locator('[data-cloud-password]')).toHaveValue('');
  await language(page,'zh');
  await expect(page.locator('[data-cloud-login-result]')).toContainText(chinese);
  expect(await page.evaluate(()=>window.cloudCalls.length)).toBe(1);
  expect(await page.evaluate(()=>window.settingsSubmissions)).toBe(0);
});

test('an incomplete login receipt cannot close the dialog or claim success from an existing account',async({page})=>{
  await openLogin(page);
  await page.locator('[data-cloud-login]').fill('test-user');
  await page.locator('[data-cloud-password]').fill('synthetic-test-password');
  await action(page,'login');
  await expect(page.locator('#cloud-login-dialog')).toBeVisible();
  await language(page,'en');
  await expect(page.locator('[data-cloud-login-result]')).toHaveText('The cloud sign-in result could not be confirmed. Refresh status before trying again.');
  expect(await page.evaluate(()=>window.cloudCalls.length)).toBe(1);
  expect(await page.evaluate(()=>window.cloudStatus.configured)).toBe(false);
});

test('registration and recovery links are visible with conventional login hierarchy',async({page},testInfo)=>{
  await page.evaluate(()=>{window.cloudStatus={configured:false,enabled:false};window.CloudPanel.update(window.cloudStatus);});
  await openLogin(page);
  await page.locator('[data-cloud-login]').fill('');
  await expect(page.locator('#cloud-login-dialog details')).toHaveCount(0);
  await expect(page.locator('[data-cloud-forgot-link]')).toBeVisible();
  await expect(page.locator('[data-cloud-signup-link] button')).toBeVisible();
  await expect(page.locator('[data-cloud-verification-link]')).toHaveCount(0);
  await expect(page.locator('[data-cloud-email-code-field]')).toBeHidden();
  await expect(page.locator('[data-cloud-action="capabilities"]')).toHaveCount(0);
  await expect(page.locator('[data-cloud-capabilities]')).toHaveCount(0);
  await expect(page.locator('[data-cloud-password-confirm]')).toBeHidden();
  await expect(page.locator('[data-cloud-password-confirm]')).toBeDisabled();
  const submit=await page.locator('[data-cloud-auth-submit]').boundingBox(),signup=await page.locator('[data-cloud-signup-link]').boundingBox();
  expect(signup.y).toBeGreaterThanOrEqual(submit.y+submit.height);
  expect(await page.evaluate(()=>window.cloudCalls)).toEqual([]);
  const screenshot=testInfo.outputPath('cloud-login-visible-links.png');
  await page.screenshot({path:screenshot});
  await testInfo.attach('cloud-login-visible-links',{path:screenshot,contentType:'image/png'});
  await language(page,'en');
  await expect(page.locator('[data-cloud-forgot-link]')).toHaveText('Forgot password?');
  await expect(page.locator('[data-cloud-signup-link]')).toHaveText("Don't have an account? Create an account");
  await expect(page.locator('[data-cloud-verification-link]')).toHaveCount(0);
});

test('forgot password activates only from its button, not the password row whitespace or label',async({page})=>{
  await openLogin(page);
  await page.locator('[data-cloud-password]').fill('unsent-password-draft');
  for(const locale of ['zh','en']){
    await language(page,locale);
    const link=await page.locator('[data-cloud-forgot-link]').boundingBox();
    const input=await page.locator('[data-cloud-password]').boundingBox();
    const heading=await page.locator('.cloud-field-heading').boundingBox();
    for(const point of [
      {x:link.x-6,y:link.y+link.height/2},
      {x:heading.x+heading.width/2,y:heading.y+heading.height/2},
      {x:input.x+input.width/2,y:input.y-4},
    ]){
      await page.mouse.click(point.x,point.y);
      await expect(page.locator('#cloud-login-dialog')).toHaveAttribute('data-view','login');
      await expect(page.locator('[data-cloud-password]')).toHaveValue('unsent-password-draft');
    }
    await page.locator('[data-cloud-password-label]').click();
    await expect(page.locator('[data-cloud-password]')).toBeFocused();
    await expect(page.locator('#cloud-login-dialog')).toHaveAttribute('data-view','login');
    await page.locator('[data-cloud-password]').click();
    await expect(page.locator('#cloud-login-dialog')).toHaveAttribute('data-view','login');
    expect(await page.locator('[data-cloud-password]').evaluate(node=>node.labels.length===1&&node.labels[0].control===node)).toBe(true);
    expect(await page.evaluate(()=>window.cloudCalls)).toEqual([]);
    await page.locator('[data-cloud-forgot-link]').click();
    await expect(page.locator('#cloud-login-dialog')).toHaveAttribute('data-view','forgot');
    expect(await page.evaluate(()=>window.cloudCalls)).toEqual([]);
    await page.locator('[data-cloud-view="login"]').click();
    await page.locator('[data-cloud-password]').fill('unsent-password-draft');
  }
  await page.locator('[data-cloud-forgot-link]').focus();
  await page.locator('[data-cloud-forgot-link]').press('Enter');
  await expect(page.locator('#cloud-login-dialog')).toHaveAttribute('data-view','forgot');
  expect(await page.evaluate(()=>window.settingsSubmissions)).toBe(0);
  expect(await page.evaluate(()=>window.cloudCalls)).toEqual([]);
});

test('registration verifies the email before displaying passwords or creating an account',async({page},testInfo)=>{
  await openLogin(page);
  await page.locator('[data-cloud-login]').fill('signup@example.test');
  await page.locator('[data-cloud-password]').fill('login-password-to-clear');
  await authView(page,'register');
  await page.locator('[data-cloud-server]').selectOption('official');
  await expect(page.locator('[data-cloud-password]')).toHaveValue('');
  await expect(page.locator('[data-cloud-login]')).toHaveValue('signup@example.test');
  await expect(page.locator('[data-cloud-login]')).toHaveAttribute('type','email');
  await expect(page.locator('[data-cloud-password]')).toBeHidden();
  await expect(page.locator('[data-cloud-password]')).toBeDisabled();
  await expect(page.locator('[data-cloud-password-confirm]')).toBeHidden();
  await expect(page.locator('[data-cloud-email-code-field]')).toBeVisible();
  await expect(page.locator('[data-cloud-forgot-link]')).toBeHidden();
  await expect(page.locator('[data-cloud-back-link]')).toBeVisible();
  await expect(page.locator('[data-cloud-verification-link]')).toHaveCount(0);
  await expect(page.locator('[data-cloud-action="capabilities"]')).toHaveCount(0);
  await expect(page.locator('[data-cloud-auth-submit]')).toBeDisabled();
  await page.locator('[data-cloud-email-code]').fill('12345');
  await expect(page.locator('[data-cloud-auth-submit]')).toBeDisabled();
  expect(await page.evaluate(()=>window.cloudCalls)).toEqual([]);
  await language(page,'en');
  await expect(page.getByRole('dialog')).toHaveAccessibleName('Create a cloud account');
  await expect(page.locator('[data-cloud-resend-code]')).toHaveText('Get code');
  await language(page,'zh');
  await page.locator('[data-cloud-email-code]').fill('');
  const registrationScreenshot=testInfo.outputPath('cloud-registration-view.png');
  await page.locator('#cloud-login-dialog').screenshot({path:registrationScreenshot});
  await testInfo.attach('cloud-registration-view',{path:registrationScreenshot,contentType:'image/png'});
  await page.locator('[data-cloud-resend-code]').click();
  await expect(page.locator('[data-cloud-open-login]')).toBeEnabled();
  await expect(page.locator('#cloud-login-dialog')).toHaveAttribute('data-view','register');
  await expect(page.locator('[data-cloud-login-result]')).toContainText(chinese);
  await expect(page.locator('[data-cloud-email-code]')).toHaveValue('');
  await expect(page.locator('[data-cloud-email-code]')).toBeFocused();
  await expect(page.locator('[data-cloud-login]')).toHaveJSProperty('readOnly',false);
  await expect(page.locator('[data-cloud-resend-code]')).toBeDisabled();
  expect(await page.evaluate(()=>window.cloudCalls)).toEqual([{action:'signup-code',origin:'https://bridge.waken.cloudns.be:8787',login:'signup@example.test'}]);
  await page.locator('[data-cloud-email-code]').fill('012345');
  await page.locator('[data-cloud-email-code]').press('Enter');
  await expect(page.locator('#cloud-login-dialog')).toHaveAttribute('data-view','password');
  await expect(page.locator('[data-cloud-password]')).toBeFocused();
  await expect(page.locator('[data-cloud-password]')).toHaveAttribute('autocomplete','new-password');
  await expect(page.locator('[data-cloud-password-confirm]')).toBeVisible();
  await expect(page.locator('[data-cloud-email-code-field]')).toBeHidden();
  await expect(page.locator('[data-cloud-login]')).toHaveJSProperty('readOnly',true);
  await expect(page.locator('[data-cloud-email-back]')).toBeVisible();
  await page.locator('[data-cloud-password]').fill('synthetic-password');
  await page.locator('[data-cloud-password-confirm]').fill('mismatched-password');
  await page.locator('[data-cloud-auth-submit]').click();
  expect(await page.evaluate(()=>window.cloudCalls.length)).toBe(2);
  expect(await page.locator('[data-cloud-password-confirm]').evaluate(node=>node.validity.customError)).toBe(true);
  await language(page,'en');
  await expect(page.getByRole('dialog')).toHaveAccessibleName('Set your password');
  expect(await page.locator('[data-cloud-password-confirm]').evaluate(node=>node.validationMessage)).toBe('Passwords do not match.');
  await page.locator('[data-cloud-password-confirm]').fill('synthetic-password');
  await language(page,'zh');
  const passwordScreenshot=testInfo.outputPath('cloud-registration-password-view.png');
  await page.locator('#cloud-login-dialog').screenshot({path:passwordScreenshot});
  await testInfo.attach('cloud-registration-password-view',{path:passwordScreenshot,contentType:'image/png'});
  await page.locator('[data-cloud-auth-submit]').click();
  await expect(page.locator('#cloud-login-dialog')).toHaveAttribute('data-view','login');
  await expect(page.locator('[data-cloud-password]')).toHaveValue('');
  await expect(page.locator('[data-cloud-password-confirm]')).toHaveValue('');
  expect(await page.evaluate(()=>window.cloudCalls)).toEqual([
    {action:'signup-code',origin:'https://bridge.waken.cloudns.be:8787',login:'signup@example.test'},
    {action:'signup-verify',origin:'https://bridge.waken.cloudns.be:8787',login:'signup@example.test',verification_code:'012345'},
    {action:'register',origin:'https://bridge.waken.cloudns.be:8787',login:'signup@example.test',registration_token:'t'.repeat(43),password:'synthetic-password'}
  ]);
  expect(await page.evaluate(()=>window.cloudStatus.configured)).toBe(false);
  expect(await page.evaluate(()=>window.settingsSubmissions)).toBe(0);
});

test('forgot password submits only the email and presents privacy-preserving feedback',async({page},testInfo)=>{
  await openLogin(page);
  await page.locator('[data-cloud-login]').fill('reset@example.test');
  await page.locator('[data-cloud-password]').fill('password-not-to-send');
  await authView(page,'forgot');
  await expect(page.locator('[data-cloud-password]')).toHaveValue('');
  await expect(page.locator('[data-cloud-password]')).toBeDisabled();
  await expect(page.locator('[data-cloud-password]')).toBeHidden();
  await expect(page.locator('[data-cloud-password-confirm]')).toBeHidden();
  await expect(page.locator('[data-cloud-email-code-field]')).toBeHidden();
  await expect(page.locator('[data-cloud-login]')).toBeFocused();
  await language(page,'en');
  await expect(page.getByRole('dialog')).toHaveAccessibleName('Reset your password');
  await expect(page.locator('[data-cloud-auth-submit]')).toHaveText('Send reset link');
  await page.locator('[data-cloud-login]').press('Enter');
  await expect(page.locator('[data-cloud-open-login]')).toBeEnabled();
  await expect(page.locator('[data-cloud-login-result]')).toHaveText('If this account exists, a password reset link will be sent to its verified email address.');
  expect(await page.evaluate(()=>window.cloudCalls)).toEqual([{action:'forgot',origin:'https://relay.example.test',login:'reset@example.test'},{action:'status'}]);
  await language(page,'zh');
  await expect(page.locator('[data-cloud-login-result]')).toContainText(chinese);
  await testInfo.attach('cloud-password-reset-view',{body:await page.screenshot(),contentType:'image/png'});
  await authView(page,'login');
  await expect(page.locator('[data-cloud-login]')).toHaveValue('reset@example.test');
  await expect(page.locator('[data-cloud-server]')).toHaveValue('custom');
  await expect(page.locator('[data-cloud-login-result]')).toBeEmpty();
  expect(await page.evaluate(()=>window.cloudCalls.length)).toBe(2);
});

test('a received code can be entered directly in registration without a separate verification link',async({page})=>{
  await openLogin(page);
  await expect(page.locator('[data-cloud-verification-link]')).toHaveCount(0);
  await page.locator('[data-cloud-login]').fill('verify@example.test');
  await authView(page,'register');
  await expect(page.locator('[data-cloud-verification-link]')).toHaveCount(0);
  await language(page,'en');
  await expect(page.locator('[data-cloud-resend-code]')).toHaveText('Get code');
  await language(page,'zh');
  expect(await page.evaluate(()=>window.cloudCalls)).toEqual([]);
  await expect(page.locator('[data-cloud-password-field]')).toBeHidden();
  await expect(page.locator('[data-cloud-back-link]')).toBeVisible();
  await expect(page.locator('[data-cloud-email-code]')).toBeVisible();
  await page.locator('[data-cloud-email-code]').fill('12345');
  await expect(page.locator('[data-cloud-auth-submit]')).toBeDisabled();
  expect(await page.evaluate(()=>window.cloudCalls)).toEqual([]);
  await page.locator('[data-cloud-email-code]').fill('12345a');
  await expect(page.locator('[data-cloud-auth-submit]')).toBeDisabled();
  expect(await page.evaluate(()=>window.cloudCalls)).toEqual([]);
  await page.locator('[data-cloud-email-code]').fill('012345');
  await language(page,'en');
  await expect(page.getByRole('dialog')).toHaveAccessibleName('Create a cloud account');
  await expect(page.locator('[data-cloud-auth-submit]')).toHaveText('Verify your email');
  await expect(page.locator('#cloud-login-help')).toHaveText('Enter your email address, get a code, then verify it before setting a password.');
  await page.locator('[data-cloud-email-code]').press('Enter');
  await expect(page.locator('[data-cloud-open-login]')).toBeEnabled();
  await expect(page.locator('#cloud-login-dialog')).toHaveAttribute('data-view','password');
  await expect(page.locator('[data-cloud-email-code]')).toHaveValue('');
  await expect(page.locator('[data-cloud-email-code-field]')).toBeHidden();
  await expect(page.locator('#cloud-login-help')).toHaveText('Email verified. Set a password to finish creating your account.');
  await expect(page.locator('[data-cloud-login-result]')).toBeEmpty();
  expect(await page.evaluate(()=>window.cloudCalls)).toEqual([{action:'signup-verify',origin:'https://relay.example.test',login:'verify@example.test',verification_code:'012345'}]);
  expect(await page.evaluate(()=>window.cloudStatus.configured)).toBe(false);
  await authView(page,'login');
  await expect(page.locator('[data-cloud-verification-link]')).toHaveCount(0);
  await authView(page,'forgot');
  await expect(page.locator('[data-cloud-verification-link]')).toHaveCount(0);
  expect(await page.evaluate(()=>window.cloudCalls.length)).toBe(1);
});

test('resending codes is confined to registration, enforces the cooldown and survives language repaint',async({page})=>{
  await openLogin(page);
  await page.locator('[data-cloud-login]').fill('resend@example.test');
  await authView(page,'register');
  await language(page,'en');
  await page.locator('[data-cloud-email-code]').fill('123456');
  await page.locator('[data-cloud-resend-code]').click();
  await expect(page.locator('[data-cloud-open-login]')).toBeEnabled();
  await expect(page.locator('[data-cloud-email-code]')).toHaveValue('');
  await expect(page.locator('[data-cloud-resend-code]')).toBeDisabled();
  await expect(page.locator('[data-cloud-resend-code]')).toHaveText('Get another code in 60s');
  await expect(page.locator('[data-cloud-login-result]')).toHaveText('If this email can be registered, a six-digit code will be sent. It expires after 10 minutes.');
  await page.locator('[data-cloud-email-code]').fill('654321');
  await language(page,'zh');
  await expect(page.locator('[data-cloud-email-code]')).toHaveValue('654321');
  await expect(page.locator('[data-cloud-resend-code]')).toHaveText(chinese);
  expect(await page.evaluate(()=>window.cloudCalls)).toEqual([{action:'signup-code',origin:'https://relay.example.test',login:'resend@example.test'}]);
  await page.evaluate(()=>{window.cloudNow+=60000;return window.cloudTick();});
  await expect(page.locator('[data-cloud-resend-code]')).toBeEnabled();
  await page.locator('[data-cloud-resend-code]').click();
  await expect(page.locator('[data-cloud-open-login]')).toBeEnabled();
  expect(await page.evaluate(()=>window.cloudCalls.length)).toBe(2);
  await authView(page,'login');
  await expect(page.locator('[data-cloud-email-code-field]')).toBeHidden();
  await authView(page,'forgot');
  await expect(page.locator('[data-cloud-email-code-field]')).toBeHidden();
  expect(await page.evaluate(()=>window.cloudCalls.length)).toBe(2);
});

test('changing the server or closing registration discards the email code without submitting it',async({page})=>{
  await openLogin(page);
  await page.locator('[data-cloud-login]').fill('scoped@example.test');
  await authView(page,'register');
  await page.locator('[data-cloud-email-code]').fill('012345');
  await page.locator('[data-cloud-server]').selectOption('official');
  await expect(page.locator('#cloud-login-dialog')).toHaveAttribute('data-view','register');
  await expect(page.locator('[data-cloud-email-code]')).toHaveValue('');
  await expect(page.locator('#cloud-login-dialog')).not.toContainText('bridge.waken.cloudns.be');
  await authView(page,'register');
  await page.locator('[data-cloud-email-code]').fill('654321');
  await closeLogin(page);
  await expect(page.locator('[data-cloud-email-code]')).toHaveValue('');
  expect(await page.evaluate(()=>window.cloudCalls)).toEqual([]);
  expect(await page.evaluate(()=>window.cloudStatus.configured)).toBe(false);
});

test('an unconfirmed code result stays in registration and never grants login or retries',async({page})=>{
  await openLogin(page);
  await page.locator('[data-cloud-login]').fill('unknown@example.test');
  await authView(page,'register');
  await page.evaluate(()=>{window.cloudResponses['signup-verify']={};window.cloudPendingAction='signup-verify';});
  await page.locator('[data-cloud-email-code]').fill('012345');
  await page.locator('[data-cloud-auth-submit]').click();
  await expect(page.locator('[data-cloud-close-login]')).toBeDisabled();
  await expect(page.locator('[data-cloud-email-code]')).toHaveValue('');
  await language(page,'en');
  await page.evaluate(()=>window.finishCloud());
  await expect(page.locator('[data-cloud-open-login]')).toBeEnabled();
  await expect(page.locator('#cloud-login-dialog')).toHaveAttribute('data-view','register');
  await expect(page.locator('[data-cloud-login-result]')).toHaveText('The email verification result could not be confirmed. Request a new code before trying again.');
  await language(page,'zh');
  expect(await page.evaluate(()=>window.cloudCalls)).toEqual([{action:'signup-verify',origin:'https://relay.example.test',login:'unknown@example.test',verification_code:'012345'}]);
  expect(await page.evaluate(()=>window.cloudStatus.configured)).toBe(false);
});

test('a legacy link receipt cannot be presented as a verification code being sent',async({page})=>{
  await authView(page,'register');
  await page.evaluate(()=>window.cloudResponses['signup-code']={accepted:true,verification_method:'link'});
  await page.locator('[data-cloud-login]').fill('legacy@example.test');
  await page.locator('[data-cloud-resend-code]').click();
  await expect(page.locator('[data-cloud-open-login]')).toBeEnabled();
  await expect(page.locator('#cloud-login-dialog')).toHaveAttribute('data-view','register');
  await language(page,'en');
  await expect(page.locator('[data-cloud-login-result]')).toHaveText('The account request could not be confirmed. Check your email and status before trying again.');
  expect(await page.evaluate(()=>window.cloudCalls.length)).toBe(1);
});

test('server-specific registration rejection stays visible and is not retried by navigation',async({page})=>{
  await page.evaluate(()=>window.cloudErrors['signup-code']='Cloud service: registration_disabled');
  await authView(page,'register');
  await page.locator('[data-cloud-login]').fill('signup@example.test');
  await page.locator('[data-cloud-resend-code]').click();
  await expect(page.locator('[data-cloud-open-login]')).toBeEnabled();
  await expect(page.locator('#cloud-login-dialog')).toHaveAttribute('data-view','register');
  await expect(page.locator('[data-cloud-login-result]')).toContainText('(registration_disabled)');
  await expect(page.locator('[data-cloud-login-result]')).toContainText(chinese);
  await language(page,'en');
  await expect(page.locator('[data-cloud-login-result]')).toContainText('Ask the server administrator');
  await authView(page,'login');
  expect(await page.evaluate(()=>window.cloudCalls.length)).toBe(1);
});

test('an unknown email request outcome is never presented as sent or automatically replayed',async({page})=>{
  await page.evaluate(()=>{window.cloudPendingAction='forgot';window.cloudResponses.forgot={};});
  await authView(page,'forgot');
  await page.locator('[data-cloud-login]').fill('reset@example.test');
  await page.locator('[data-cloud-auth-submit]').click();
  await expect(page.locator('[data-cloud-back-link] button')).toBeDisabled();
  await expect(page.locator('[data-cloud-close-login]')).toBeDisabled();
  await page.evaluate(()=>window.finishCloud());
  await expect(page.locator('[data-cloud-open-login]')).toBeEnabled();
  await language(page,'en');
  await expect(page.locator('[data-cloud-login-result]')).toHaveText('The account request could not be confirmed. Check your email and status before trying again.');
  expect(await page.evaluate(()=>window.cloudCalls.length)).toBe(1);
  await language(page,'zh');
  expect(await page.evaluate(()=>window.cloudCalls.length)).toBe(1);
});

test('email changes clear the code and preserve each email cooldown without sending a password',async({page})=>{
  await authView(page,'register');
  await page.locator('[data-cloud-login]').fill('first@example.test');
  await page.locator('[data-cloud-resend-code]').click();
  await expect(page.locator('[data-cloud-resend-code]')).toBeDisabled();
  await page.locator('[data-cloud-email-code]').fill('012345');
  await page.locator('[data-cloud-login]').fill('second@example.test');
  await expect(page.locator('[data-cloud-email-code]')).toHaveValue('');
  await expect(page.locator('[data-cloud-resend-code]')).toBeEnabled();
  await page.locator('[data-cloud-login]').fill('first@example.test');
  await expect(page.locator('[data-cloud-resend-code]')).toBeDisabled();
  await page.locator('[data-cloud-login]').fill('not-an-email');
  await expect(page.locator('[data-cloud-resend-code]')).toBeDisabled();
  await expect(page.locator('[data-cloud-auth-submit]')).toBeDisabled();
  expect(await page.evaluate(()=>window.cloudCalls)).toEqual([{action:'signup-code',origin:'https://relay.example.test',login:'first@example.test'}]);
});

test('password drafts and email proof survive language changes but not a server change',async({page})=>{
  await verifySignup(page);
  await page.locator('[data-cloud-password]').fill('synthetic-password');
  await page.locator('[data-cloud-password-confirm]').fill('synthetic-password');
  await language(page,'en');
  await expect(page.locator('[data-cloud-password]')).toHaveValue('synthetic-password');
  await expect(page.locator('[data-cloud-auth-submit]')).toHaveText('Complete registration');
  await language(page,'zh');
  await expect(page.locator('[data-cloud-password-confirm]')).toHaveValue('synthetic-password');
  await page.locator('[data-cloud-server]').selectOption('official');
  await expect(page.locator('#cloud-login-dialog')).toHaveAttribute('data-view','register');
  await expect(page.locator('[data-cloud-password]')).toHaveValue('');
  await expect(page.locator('[data-cloud-password-confirm]')).toHaveValue('');
  await expect(page.locator('[data-cloud-password]')).toBeHidden();
  expect(await page.evaluate(()=>window.cloudCalls.length)).toBe(1);
});

test('expired or dismissed email proof cannot be reused to set a password',async({page})=>{
  await verifySignup(page);
  await page.locator('[data-cloud-password]').fill('synthetic-password');
  await page.evaluate(()=>{window.cloudNow+=600001;return window.cloudTick();});
  await expect(page.locator('#cloud-login-dialog')).toHaveAttribute('data-view','register');
  await expect(page.locator('[data-cloud-password]')).toHaveValue('');
  await expect(page.locator('[data-cloud-email-code]')).toHaveValue('');
  await expect(page.locator('[data-cloud-auth-submit]')).toBeDisabled();
  await language(page,'en');
  await expect(page.locator('[data-cloud-login-result]')).toHaveText('Email verification has expired or already been used. Verify your email again.');
  await verifySignup(page);
  await page.locator('[data-cloud-password]').fill('another-synthetic-password');
  await closeLogin(page);
  await authView(page,'register');
  await expect(page.locator('[data-cloud-password]')).toBeHidden();
  await expect(page.locator('[data-cloud-password]')).toHaveValue('');
  expect(await page.evaluate(()=>window.cloudCalls.length)).toBe(2);
  const storage=await page.evaluate(()=>JSON.stringify({local:{...localStorage},session:{...sessionStorage}}));
  expect(storage).not.toContain('t'.repeat(43));
});

test('changing the verified email requires a new proof and never sends a stale password',async({page})=>{
  await verifySignup(page);
  await page.locator('[data-cloud-password]').fill('synthetic-password');
  await page.locator('[data-cloud-restart-registration]').click();
  await expect(page.locator('#cloud-login-dialog')).toHaveAttribute('data-view','register');
  await expect(page.locator('[data-cloud-login]')).toHaveJSProperty('readOnly',false);
  await expect(page.locator('[data-cloud-password]')).toHaveValue('');
  await page.locator('[data-cloud-login]').fill('different@example.test');
  await expect(page.locator('[data-cloud-auth-submit]')).toBeDisabled();
  expect(await page.evaluate(()=>window.cloudCalls.length)).toBe(1);
});

test('unknown registration results discard the one-time proof and require checking login first',async({page})=>{
  await verifySignup(page);
  await page.evaluate(()=>{window.cloudResponses.register={};window.cloudPendingAction='register';});
  await page.locator('[data-cloud-password]').fill('synthetic-password');
  await page.locator('[data-cloud-password-confirm]').fill('synthetic-password');
  await page.locator('[data-cloud-auth-submit]').click();
  await expect(page.locator('[data-cloud-close-login]')).toBeDisabled();
  await expect(page.locator('[data-cloud-password]')).toHaveValue('');
  await expect(page.locator('[data-cloud-password-confirm]')).toHaveValue('');
  await page.evaluate(()=>window.finishCloud());
  await expect(page.locator('#cloud-login-dialog')).toHaveAttribute('data-view','login');
  await language(page,'en');
  await expect(page.locator('[data-cloud-login-result]')).toHaveText('The registration result could not be confirmed. Try signing in or resetting your password before registering again.');
  await language(page,'zh');
  await authView(page,'register');
  await expect(page.locator('[data-cloud-password-field]')).toBeHidden();
  await expect(page.locator('[data-cloud-email-code]')).toHaveValue('');
  expect(await page.evaluate(()=>window.cloudCalls.map(call=>call.action))).toEqual(['signup-verify','register']);
  expect(await page.evaluate(()=>window.cloudStatus.configured)).toBe(false);
});

test('web access switches independently and has translated stable feedback',async({page},testInfo)=>{
  await page.evaluate(()=>window.CloudPanel.update(window.cloudStatus));
  const control=page.getByRole('switch',{name:'允许网页访问本电脑'});
  await expect(control).not.toBeChecked();
  await control.check();
  await expect(control).toBeChecked();
  await expect(page.locator('[data-cloud-web-state]')).toHaveText('已允许当前云账号通过网页访问。');
  expect(await page.evaluate(()=>window.cloudStatus.bindings[0].allowed)).toBe(true);
  await control.uncheck();
  await expect(page.locator('[data-cloud-result]')).toHaveText('网页访问已关闭，已打开的网页会话已断开。');
  expect(await page.evaluate(()=>window.cloudAllCalls.filter(c=>c.action==='web-access'))).toEqual([
    {action:'web-access',origin:'https://relay.example.test',account_id:'account-original',gateway_id:'gateway-original',allowed:true,expected_revision:0},
    {action:'web-access',origin:'https://relay.example.test',account_id:'account-original',gateway_id:'gateway-original',allowed:false,expected_revision:1}
  ]);
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
  await page.screenshot({path:path.join(root,'.tmp/web-access-20261108',testInfo.project.name+'.png'),fullPage:true});
  await language(page,'en');
  await expect(page.getByRole('switch',{name:'Allow web access to this computer'})).not.toBeChecked();
  await expect(page.locator('[data-cloud-web-state]')).toHaveText('Web access disabled. Enable it here to connect from a browser.');
});

test('a boolean verification receipt without a registration proof cannot unlock password entry',async({page})=>{
  await page.evaluate(()=>window.cloudResponses['signup-verify']={verified:true});
  await authView(page,'register');
  await page.locator('[data-cloud-login]').fill('signup@example.test');
  await page.locator('[data-cloud-email-code]').fill('012345');
  await page.locator('[data-cloud-auth-submit]').click();
  await expect(page.locator('[data-cloud-open-login]')).toBeEnabled();
  await expect(page.locator('#cloud-login-dialog')).toHaveAttribute('data-view','register');
  await expect(page.locator('[data-cloud-password-field]')).toBeHidden();
  expect(await page.evaluate(()=>window.cloudCalls.length)).toBe(1);
});
