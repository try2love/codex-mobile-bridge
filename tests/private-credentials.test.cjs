'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {PrivateCredentials}=require('../desktop/shared/private-credentials.cjs');
const root=path.resolve(__dirname,'../.tmp');
function fixture(t){const profile=fs.mkdtempSync(path.join(root,'private-vault-'));t.after(()=>fs.rmSync(profile,{recursive:true,force:true}));return new PrivateCredentials(profile,'test');}
test('profile encryption survives new controller identities, changes nonce and binds purpose',{skip:process.platform==='win32'},async t=>{
 const a=fixture(t),first=await a.encryptStringAsync('synthetic-private-secret'),second=await a.encryptStringAsync('synthetic-private-secret');
 assert.notDeepEqual(first,second);assert.equal(first.includes(Buffer.from('synthetic-private-secret')),false);
 const other=new PrivateCredentials(path.dirname(a.directory),'test');assert.equal((await other.decryptStringAsync(first)).result,'synthetic-private-secret');
 await assert.rejects(new PrivateCredentials(path.dirname(a.directory),'other').decryptStringAsync(first));
 assert.equal(fs.statSync(a.directory).mode&0o777,0o700);assert.equal(fs.statSync(path.join(a.directory,'key')).mode&0o777,0o600);
});
test('tampering and missing keys fail without replacing ciphertext or regenerating a key',{skip:process.platform==='win32'},async t=>{
 const a=fixture(t),value=await a.encryptStringAsync('fixture'),corrupt=Buffer.from(value);corrupt[20]^=1;
 await assert.rejects(a.decryptStringAsync(corrupt));
 const auth=path.join(path.dirname(a.directory),'zcode-official-auth.enc');fs.writeFileSync(auth,value,{mode:0o600});fs.unlinkSync(path.join(a.directory,'key'));
 await assert.rejects(a.decryptStringAsync(value));await assert.rejects(a.encryptStringAsync('replacement'));
 assert.equal(fs.existsSync(path.join(a.directory,'key')),false);assert.deepEqual(fs.readFileSync(auth),value);
});
test('credential directory and key symlinks or public permissions are refused',{skip:process.platform==='win32'},async t=>{
 const a=fixture(t),encrypted=await a.encryptStringAsync('fixture');const file=path.join(a.directory,'key');fs.chmodSync(file,0o644);await assert.rejects(a.decryptStringAsync(encrypted));
 fs.chmodSync(file,0o600);fs.renameSync(file,file+'.old');fs.symlinkSync(file+'.old',file);await assert.rejects(a.encryptStringAsync('fixture'));
 fs.unlinkSync(file);fs.renameSync(file+'.old',file);fs.chmodSync(a.directory,0o755);assert.equal(await a.isAsyncEncryptionAvailable(),false);
});

test('truncated or unknown ciphertext and indexed local data never permit key regeneration',{skip:process.platform==='win32'},async t=>{
 for(const original of [Buffer.from('CMB1truncated'),Buffer.from('unknown-format')]){
  const a=fixture(t);a.folder();const auth=path.join(path.dirname(a.directory),'zcode-official-auth.enc');fs.writeFileSync(auth,original,{mode:0o600});
  await assert.rejects(a.encryptStringAsync('replacement'));assert.equal(fs.existsSync(path.join(a.directory,'key')),false);assert.deepEqual(fs.readFileSync(auth),original);
 }
 const a=fixture(t);fs.writeFileSync(path.join(path.dirname(a.directory),'connection-vault.json'),JSON.stringify({one:'gateway-v1'}),{mode:0o600});
 await assert.rejects(a.encryptStringAsync('replacement'));assert.equal(fs.existsSync(path.join(a.directory,'key')),false);
});
test('commit verification rejects a key replaced after encryption',{skip:process.platform==='win32'},async t=>{
 const a=fixture(t),encrypted=await a.encryptStringAsync('fixture'),key=path.join(a.directory,'key'),foreign=Buffer.alloc(32,42);fs.writeFileSync(key,foreign);
 assert.throws(()=>a.assertCurrent(encrypted),/密钥已变化/);assert.deepEqual(fs.readFileSync(key),foreign);
});
