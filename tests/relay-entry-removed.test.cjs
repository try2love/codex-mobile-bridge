'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const read=name=>fs.readFileSync(path.join(__dirname,'..',name),'utf8');
test('invitation relay has no desktop entry, IPC command or automatic gateway connection',()=>{
  for(const name of ['desktop/index.html','desktop/preload.cjs','desktop/main.cjs','desktop/shared/controller.cjs','desktop.py','bridge/app/desktop.py','run.py']){
    assert.doesNotMatch(read(name),/shared[-_]relay|自建中继 · 内测/,name);
  }
  assert.ok(!fs.existsSync(path.join(__dirname,'../desktop/features/connections/shared-relay.js')));
});
