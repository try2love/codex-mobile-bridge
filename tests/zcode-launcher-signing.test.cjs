'use strict';
const {test}=require('node:test'),assert=require('node:assert/strict'),crypto=require('node:crypto');
const hook=require('../scripts/sign-zcode-launcher.cjs');
const {publicKey,privateKey}=crypto.generateKeyPairSync('ed25519');
const pem=publicKey.export({format:'pem',type:'spki'});
test('production component proof is bound to the existing publisher and exact payload',()=>{
  const record={schema:1,purpose:'cmb-zcode-local-components',components:{broker:'original'}};
  const envelope=JSON.parse(hook.authorize(record,privateKey,pem));
  const payload=Buffer.from(envelope.payload,'base64'),signature=Buffer.from(envelope.signature,'base64');
  assert.deepEqual(JSON.parse(payload),record);
  assert.equal(crypto.verify(null,payload,publicKey,signature),true);
  assert.equal(crypto.verify(null,Buffer.from('changed'),publicKey,signature),false);
  const other=crypto.generateKeyPairSync('ed25519');
  assert.throws(()=>hook.authorize(record,other.privateKey,pem),/matching release signing key/);
  assert.throws(()=>hook.authorize(record,null,pem),/matching release signing key/);
});
test('non-Mac and independent Test packaging remain unchanged',async()=>{
  await hook({electronPlatformName:'win32'});
  await hook({electronPlatformName:'linux'});
  await hook({electronPlatformName:'darwin',packager:{appInfo:{id:'io.github.try2love.codexmobilebridge.test'}}});
});
