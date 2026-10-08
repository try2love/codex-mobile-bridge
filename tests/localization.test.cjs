const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs');
const i18n=require('../web/i18n.js');
test('all declared mobile HTML labels have English translations',()=>{
 const html=fs.readFileSync(__dirname+'/../web/index.html','utf8');
 for(const m of html.matchAll(/data-i18n(?:-[a-z-]+)?="([^"]+)"/g))assert.ok(Object.hasOwn(i18n.dictionary,m[1]),m[1]);
});
test('workbench literal translation keys are covered',()=>{
 for(const f of ['workbench','git-panel','side-chat','terminal-panel','floating-panel','agents-panel','list-sync'])
  for(const m of fs.readFileSync(__dirname+'/../web/'+f+'.js','utf8').matchAll(/BridgeI18n.t\('([^']*[\u4e00-\u9fff][^']*)'\)/g))assert.ok(Object.hasOwn(i18n.dictionary,m[1]),f+': '+m[1]);
});
test('client readiness errors and live notices translate in both desktop and web',()=>{
 const desktop=require('../desktop/i18n.js');
 for(const source of ['启动网关后检查连接状态','请在电脑端安装 Codex 并配置账号或 API','请在桌面配置账号或 API；旧版插件请重新安装','客户端连接不可用，请在电脑端检查']){
   i18n.setLanguage('en');assert.doesNotMatch(i18n.t(source),/[\u4e00-\u9fff]/,source);
   assert.equal(desktop.translate("Error invoking remote method 'bridge:desktop-sessions': Error: "+source,'en'),i18n.t(source));
 }
 i18n.setLanguage('zh');
});
test('desktop module markup has translations, including dynamically inserted controls',()=>{
 for(const file of ['desktop/index.html','desktop/layout.js'])
  for(const m of fs.readFileSync(__dirname+'/../'+file,'utf8').matchAll(/data-i18n(?:-[a-z-]+)?="([^"]+)"/g))assert.ok(Object.hasOwn(i18n.dictionary,m[1]),file+': '+m[1]);
});
