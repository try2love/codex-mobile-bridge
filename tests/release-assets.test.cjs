'use strict';
const test=require('node:test'),assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path');
const {generateKeyPairSync,createHash,sign}=require('node:crypto');
const {releaseAssetNames,currentReleaseNotes,writeChecksums,verifyRelease}=require('../scripts/release-assets.cjs');
const {assetName}=require('../desktop/features/updates/updater.cjs');
const version=require('../package.json').version;
function fixture(t){
  const parent=path.join(__dirname,'../.tmp');fs.mkdirSync(parent,{recursive:true});
  const directory=fs.mkdtempSync(path.join(parent,'release-assets-'));
  t.after(()=>fs.rmSync(directory,{recursive:true,force:true}));
  for(const name of releaseAssetNames())fs.writeFileSync(path.join(directory,name),'fixture '+name);
  const keys=generateKeyPairSync('ed25519'),assets={};
  for(const [platform,arch] of [['darwin','arm64'],['darwin','x64'],['win32','x64']]){
    const name=assetName(version,platform,arch),bytes=fs.readFileSync(path.join(directory,name));
    assets[`${platform}-${arch}`]={name,size:bytes.length,sha256:createHash('sha256').update(bytes).digest('hex')};
  }
  const payload=Buffer.from(JSON.stringify({schema:1,version,notes:'Current release only',assets}));
  fs.writeFileSync(path.join(directory,'bridge-update.json'),JSON.stringify({payload:payload.toString('base64'),signature:sign(null,payload,keys.privateKey).toString('base64')}));
  writeChecksums(directory);
  return {directory,key:keys.publicKey};
}
test('current release notes preserve both languages and exclude historical entries',()=>{
  const text='## v2.0.0 · Release\n\nChinese\n### English\nEnglish\n\n---\n\n## v1.4.0\nOld';
  assert.equal(currentReleaseNotes(text,'2.0.0'),'## v2.0.0 · Release\n\nChinese\n### English\nEnglish\n');
  assert.throws(()=>currentReleaseNotes(text,'2.0.1'),/Missing release notes/);
  assert.throws(()=>currentReleaseNotes('## v2x0x0\nWrong','2.0.0'),/Missing release notes/);
});
test('a complete signed desktop/mobile release has 14 checksummed assets',t=>{
  const {directory,key}=fixture(t);
  assert.deepEqual(verifyRelease(directory,{key}),{version,assets:14,checksums:14});
  const sums=fs.readFileSync(path.join(directory,'SHA256SUMS.txt'),'utf8');
  for(const name of ['Android.apk','iOS-unsigned.ipa','iOS-source.zip','bridge-update.json'])assert.ok(sums.includes(name));
});
test('desktop-only release cannot pass final verification or replace checksums',t=>{
  const {directory,key}=fixture(t),before=fs.readFileSync(path.join(directory,'SHA256SUMS.txt'),'utf8');
  fs.unlinkSync(path.join(directory,`Codex-Mobile-Bridge-${version}-Android.apk`));
  assert.throws(()=>verifyRelease(directory,{key,write:true}),/incomplete/);
  assert.equal(fs.readFileSync(path.join(directory,'SHA256SUMS.txt'),'utf8'),before);
});
test('checksum failures, missing coverage and duplicate entries are rejected',t=>{
  const {directory,key}=fixture(t),sumFile=path.join(directory,'SHA256SUMS.txt');
  const original=fs.readFileSync(sumFile,'utf8');
  const mobile=path.join(directory,`Codex-Mobile-Bridge-${version}-Android.apk`);
  fs.appendFileSync(mobile,'modified');
  assert.throws(()=>verifyRelease(directory,{key}),/Checksum mismatch/);
  assert.equal(verifyRelease(directory,{key,write:true}).assets,14);
  fs.writeFileSync(sumFile,fs.readFileSync(sumFile,'utf8').split('\n').slice(1).join('\n'));
  assert.throws(()=>verifyRelease(directory,{key}),/cover every/);
  fs.writeFileSync(sumFile,original+original.split('\n')[0]+'\n');
  fs.writeFileSync(mobile,'fixture '+path.basename(mobile));
  assert.throws(()=>verifyRelease(directory,{key}),/duplicate/);
});
test('recomputed checksums cannot conceal a changed desktop ZIP or wrong signing identity',t=>{
  const {directory,key}=fixture(t);
  assert.throws(()=>verifyRelease(directory,{key:generateKeyPairSync('ed25519').publicKey,write:true}),/签名/);
  fs.appendFileSync(path.join(directory,assetName(version,'darwin','arm64')),'modified');
  writeChecksums(directory);
  assert.throws(()=>verifyRelease(directory,{key,write:true}),/Signed update asset mismatch/);
});
test('release workflow prepares a draft and uses the packaged signing identity',()=>{
  const workflow=fs.readFileSync(path.join(__dirname,'../.github/workflows/release.yml'),'utf8');
  assert.ok(workflow.includes('desktop/features/updates/update-public-key.pem'));
  assert.ok(workflow.includes('release-assets.cjs notes'));
  assert.ok(workflow.includes('--draft --title'));
  assert.ok(!workflow.includes('--draft=false'));
  assert.ok(!workflow.includes('inputs.publish'));
});
