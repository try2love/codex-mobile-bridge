'use strict';
const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const i18n=require('../web/shared/i18n.js');
const desktop=require('../desktop/shared/i18n.js');
const source=fs.readFileSync(path.join(__dirname,'../desktop/features/connections/cloud.js'),'utf8');

function messages(name){
  const declaration=source.match(new RegExp('const '+name+'=(\\{[\\s\\S]*?\\});'));
  assert.ok(declaration,'Missing cloud message map: '+name);
  return Object.values(vm.runInNewContext('('+declaration[1]+')'));
}

test('cloud static controls, runtime codes and validation messages share the UI dictionary',()=>{
  const labels=new Set([
    ...Array.from(source.matchAll(/data-i18n="([^"]+)"/g),match=>match[1]),
    ...Array.from(source.matchAll(/\bt\('([^']+)'\)/g),match=>match[1]),
    ...Array.from(source.matchAll(/\bformat\('([^']+)'/g),match=>match[1]),
    ...messages('runtimeMessages'),...messages('errors'),...messages('validationMessages'),...messages('fieldLabels'),
    ...messages('authTitles'),...messages('authHelp'),...messages('authSubmits'),...messages('completionMessages'),...messages('serviceMessages'),
    'Working...',
    'Done. If a write times out, refresh status; do not blindly replay it.',
    'The cloud sign-in result could not be confirmed. Refresh status before trying again.',
    'The cloud sign-out result could not be confirmed. Refresh status before trying again.',
    'The account request could not be confirmed. Check your email and status before trying again.',
    'The email verification result could not be confirmed. Request a new code before trying again.',
    'The registration result could not be confirmed. Try signing in or resetting your password before registering again.',
    'Account created. Sign in to continue.'
  ]);
  const previous=i18n.language();
  try{
    for(const label of labels){
      assert.ok(i18n.source(label),'Missing translation: '+label);
      i18n.setLanguage('zh');
      const chinese=i18n.t(label);
      assert.match(chinese,/[\u4e00-\u9fff]/,label);
      i18n.setLanguage('en');
      assert.equal(i18n.t(label),label);
      assert.equal(desktop.translate(chinese,'en'),label);
      assert.deepEqual(Array.from(chinese.matchAll(/\{\w+\}/g),m=>m[0]),Array.from(label.matchAll(/\{\w+\}/g),m=>m[0]),label);
    }
  }finally{i18n.setLanguage(previous);}
});

test('cloud sign-in is an isolated modal with the official server first and custom address optional',()=>{
  assert.match(source,/document\.body\.append\(dialog\)/);
  assert.match(source,/data-cloud-open-login/);
  assert.match(source,/data-cloud-login-form/);
  assert.match(source,/<select data-cloud-server><option value="official"[^>]*>Official Cloud<\/option><option value="custom"/);
  assert.match(source,/dialog\.showModal\(\)/);
  assert.doesNotMatch(source,/data-cloud-official-address|\$\{officialOrigin\}/);
  assert.match(source,/event\.preventDefault\(\);event\.stopPropagation\(\);if\(dialog\.open\)call\(authAction\(\)\)/);
});

test('registration, password recovery and email verification use visible entry points rather than drawers',()=>{
  const markup=source.split('dialog.innerHTML=`')[1].split('`;')[0];
  assert.doesNotMatch(markup,/<details|<summary|data-cloud-account-help/);
  for(const view of ['register','forgot'])assert.match(markup,new RegExp('data-cloud-view="'+view+'"'));
  assert.match(source,/\$\('password-confirm'\)\.setCustomValidity/);
  assert.match(source,/result\.accepted!==true/);
  assert.match(source,/authView='password'/);
  assert.match(source,/if\(action==='register'\)authView='login'/);
});

test('email verification belongs to account creation and server detection has no UI control',()=>{
  const markup=source.split('dialog.innerHTML=`')[1].split('`;')[0];
  assert.doesNotMatch(markup,/data-cloud-action="capabilities"|data-cloud-capabilities|cloud-server-tools/);
  assert.doesNotMatch(markup,/data-cloud-verification-link|data-cloud-view="verification"/);
  assert.match(markup,/data-cloud-resend-code data-cloud-action="signup-code"/);
  assert.match(source,/\$\('email-code-field'\)\.hidden=!register/);
  assert.match(source,/needsPassword=login\|\|password/);
  assert.match(source,/view==='password'\)return/);
  assert.match(source,/\$\('redeem'\)\.hidden=!\(value\.configured&&entitlement\?\.required===true\)/);
  assert.match(markup,/autocomplete="one-time-code" pattern="\[0-9\]\{6\}"/);
  assert.match(source,/result\.verification_method!=='code'/);
  assert.match(source,/payload\.verification_code=\$\('email-code'\)\.value\.trim\(\);\$\('email-code'\)\.value=''/);
  assert.match(source,/payload\.registration_token=registrationProof\.token;registrationProof=null/);
  assert.doesNotMatch(source,/new verification link|Resend verification email/);
});

test('cloud repaint subscribes to the shared language event without adding an API call',()=>{
  assert.match(source,/document\.addEventListener\('bridge-language',render\)/);
  const render=source.split('function render(){')[1].split('function renderAuth(')[0];
  assert.doesNotMatch(render,/api\.cloud\(/);
  assert.match(source,/toLocaleString\(BridgeI18n\.locale\(\)\)/);
  assert.match(source,/expected!==accountScope\(\)\|\|revision!==directoryRevision/);
});

test('cloud redemption button has spacing below its input without changing other actions',()=>{
  const css=fs.readFileSync(path.join(__dirname,'../desktop/shell/layout.css'),'utf8');
  assert.match(css,/#cloud-account \[data-cloud-redeem\]>button\{margin-top:12px\}/);
  assert.match(source,/<div data-cloud-redeem hidden><label>[\s\S]*?<input data-cloud-code[^>]*><\/label><button type="button" data-cloud-action="redeem"/);
});
