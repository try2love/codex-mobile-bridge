'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {publishLanguage}=require('../desktop/shared/gateway-language.cjs');
test('gateway language persists independently of credentials and updates atomically',t=>{
 fs.mkdirSync(path.join(__dirname,'../.tmp'),{recursive:true});
 const dir=fs.mkdtempSync(path.join(__dirname,'../.tmp/language-'));t.after(()=>fs.rmSync(dir,{recursive:true,force:true}));
 const credentials=path.join(dir,'config.json');fs.writeFileSync(credentials,'fixture');
 for(const [input,expected] of [['en','en'],['zh-CN','zh']]){publishLanguage(dir,input);assert.deepEqual(JSON.parse(fs.readFileSync(path.join(dir,'ui-language.json'),'utf8')),{language:expected});}
 assert.throws(()=>publishLanguage(dir,'invalid'));assert.equal(fs.readFileSync(credentials,'utf8'),'fixture');
 assert.equal(fs.existsSync(path.join(dir,'ui-language.json.tmp')),false);
});
