"use strict";
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {readAppearance,saveAppearance}=require('../desktop/shell/appearance.cjs');
test('desktop theme survives a new reader and invalid changes preserve the saved preference',t=>{
 const directory=fs.mkdtempSync(path.resolve(__dirname,'../.tmp/appearance-'));t.after(()=>fs.rmSync(directory,{recursive:true,force:true}));
 assert.equal(readAppearance(directory),'system');
 for(const theme of ['dark','light','system']){saveAppearance(directory,theme);assert.equal(readAppearance(directory),theme);}
 const file=path.join(directory,'desktop-appearance.json'),before=fs.readFileSync(file);
 assert.throws(()=>saveAppearance(directory,'unknown'),/主题/);assert.deepEqual(fs.readFileSync(file),before);
 if(process.platform!=='win32')assert.equal(fs.statSync(file).mode&0o777,0o600);
 assert.deepEqual(fs.readdirSync(directory),['desktop-appearance.json']);
 fs.writeFileSync(file,'broken');assert.equal(readAppearance(directory),'system');
});
