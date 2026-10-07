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
