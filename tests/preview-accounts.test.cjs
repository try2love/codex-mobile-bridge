'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {seedPreviewAccounts}=require('../desktop/preview-accounts.cjs');
const base=path.join(__dirname,'../.tmp');fs.mkdirSync(base,{recursive:true});
function fixture(t){
  const root=fs.mkdtempSync(path.join(base,'preview-accounts-'));t.after(()=>fs.rmSync(root,{recursive:true,force:true}));
  const source=path.join(root,'stable'),target=path.join(root,'preview'),a='a'.repeat(32),b='b'.repeat(32);
  const write=(dir,file,value)=>{const out=path.join(dir,'accounts',file);fs.mkdirSync(path.dirname(out),{recursive:true});fs.writeFileSync(out,JSON.stringify(value));};
  const index={accounts:[{id:a,kind:'chatgpt',name:'Official'},{id:b,kind:'api',name:'API'}],activeId:a,activeStamp:{config:'fixture'},desktopExecutable:'/fixture/Codex'};
  write(source,'index.json',index);write(source,a+'/auth.json',{tokens:{fixture:'official'}});write(source,a+'/config.toml','not needed');write(source,a+'/state_5.sqlite','must not copy');write(source,b+'/api.json',{key:'fixture-api-key'});
  write(source,'switch.json',{phase:'complete'});write(source,'requests.json',{fixture:'must not copy'});
  return {source,target,a,b,index,write,seed:()=>seedPreviewAccounts(source,target)};
}
test('new preview inherits saved accounts and credentials without runtime databases or switch state',t=>{
  const f=fixture(t);assert.equal(f.seed(),2);
  const saved=JSON.parse(fs.readFileSync(path.join(f.target,'accounts/index.json')));assert.deepEqual(saved,f.index);
  for(const [id,file] of [[f.a,'auth.json'],[f.b,'api.json']]){
    const target=path.join(f.target,'accounts',id,file);assert.deepEqual(fs.readFileSync(target),fs.readFileSync(path.join(f.source,'accounts',id,file)));
    if(process.platform!=='win32')assert.equal(fs.statSync(target).mode&0o777,0o600);
  }
  assert.deepEqual(fs.readdirSync(path.join(f.target,'accounts',f.a)),['auth.json']);
  assert.equal(fs.existsSync(path.join(f.target,'accounts/switch.json')),false);
  assert.equal(fs.existsSync(path.join(f.target,'accounts/requests.json')),false);
});
test('initial import preserves existing preview records and never modifies the stable source',t=>{
  const f=fixture(t),original=fs.readFileSync(path.join(f.source,'accounts/index.json'));
  f.write(f.target,'index.json',{accounts:[{...f.index.accounts[1],name:'Preview edit'}],activeId:f.b});f.write(f.target,f.b+'/api.json',{key:'preview-key'});
  assert.equal(f.seed(),1);const saved=JSON.parse(fs.readFileSync(path.join(f.target,'accounts/index.json')));
  assert.equal(saved.accounts[0].name,'Preview edit');assert.equal(saved.activeId,f.b);
  assert.deepEqual(JSON.parse(fs.readFileSync(path.join(f.target,'accounts',f.b,'api.json'))),{key:'preview-key'});
  assert.deepEqual(fs.readFileSync(path.join(f.source,'accounts/index.json')),original);
});
test('restarting does not overwrite or resurrect accounts removed from the preview',t=>{
  const f=fixture(t);f.seed();f.write(f.target,'index.json',{accounts:[],activeId:null});
  assert.equal(f.seed(),0);assert.deepEqual(JSON.parse(fs.readFileSync(path.join(f.target,'accounts/index.json'))).accounts,[]);
});
test('missing source or in-progress account switching defers inheritance',t=>{
  const f=fixture(t);assert.equal(seedPreviewAccounts(f.source+'-missing',f.target),0);
  f.write(f.source,'switch.json',{phase:'applying'});assert.equal(f.seed(),0);assert.equal(fs.existsSync(path.join(f.target,'accounts/index.json')),false);
});
test('incomplete credentials fail before writing an account index',t=>{
  const f=fixture(t);fs.unlinkSync(path.join(f.source,'accounts',f.b,'api.json'));
  assert.throws(f.seed);assert.equal(fs.existsSync(path.join(f.target,'accounts/index.json')),false);
});
