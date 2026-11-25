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
  for(const [platform,arch,format] of [['darwin','arm64'],['darwin','x64'],['win32','x64'],
    ['linux','x64','deb'],['linux','x64','AppImage'],['linux','arm64','deb'],['linux','arm64','AppImage']]){
    const name=assetName(version,platform,arch,format),bytes=fs.readFileSync(path.join(directory,name));
    assets[`${platform}-${arch}${format?'-'+format:''}`]={name,size:bytes.length,sha256:createHash('sha256').update(bytes).digest('hex')};
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
test('Linux installer integrity is authenticated even when release checksums are recomputed',t=>{
  const {directory,key}=fixture(t);
  fs.appendFileSync(path.join(directory,assetName(version,'linux','x64','deb')),'modified');
  writeChecksums(directory);
  assert.throws(()=>verifyRelease(directory,{key}),/Signed update asset mismatch/);
});
test('release workflow prepares a draft and uses the packaged signing identity',()=>{
  const workflow=fs.readFileSync(path.join(__dirname,'../.github/workflows/release.yml'),'utf8');
  assert.ok(workflow.includes('desktop/features/updates/update-public-key.pem'));
  assert.ok(workflow.includes('release-assets.cjs notes'));
  assert.ok(workflow.includes('--draft --title'));
  assert.ok(!workflow.includes('--draft=false'));
  assert.ok(!workflow.includes('inputs.publish'));
});
test('release caller grants reusable desktop read permissions and keeps ordinary builds as the default',()=>{
  const yaml=require('js-yaml'),load=name=>yaml.load(fs.readFileSync(path.join(__dirname,'../.github/workflows/'+name),'utf8'));
  const release=load('release.yml'),desktop=load('desktop.yml'),build=release.jobs.build;
  assert.equal(build.uses,'./.github/workflows/desktop.yml');
  assert.deepEqual(build.permissions,desktop.permissions);
  assert.deepEqual(build.permissions,{contents:'read',actions:'read'});
  assert.deepEqual(release.permissions,{contents:'read'});
  assert.equal(release.jobs.ios.permissions,undefined);
  assert.deepEqual(release.jobs.release.permissions,{contents:'write'});
  assert.equal(build.with.acceptance_only,undefined);
  for(const entry of ['workflow_call','workflow_dispatch'])assert.equal(desktop.on[entry].inputs.acceptance_only.default,false);
  assert.equal(desktop.jobs.package.if,'inputs.acceptance_only != true');
  assert.equal(desktop.jobs.linux.if,"inputs.acceptance_only != true && inputs.build_target != 'intel' && inputs.build_target != 'mac-windows-reuse' && inputs.build_target != 'windows-reuse' && inputs.build_target != 'mac-arm-reuse'");
  assert.equal(desktop.on.workflow_dispatch.inputs.build_target.default,'all');
  assert.deepEqual(desktop.on.workflow_dispatch.inputs.build_target.options,['all','intel','mac-windows-reuse','windows-reuse','mac-arm-reuse']);
  assert.equal(desktop.on.workflow_call.inputs.build_target.default,'all');
  const matrix=desktop.jobs.package.strategy.matrix.include;
  assert.ok(matrix.includes("inputs.build_target == 'intel'"));
  const selected=JSON.parse(matrix.match(/inputs.build_target == 'mac-windows-reuse' && '([^']+)'/)[1]);
  assert.deepEqual(selected,[{os:'macos-15',arch:'arm64','app-dir':'mac-arm64'},{os:'windows-latest',arch:'x64'}]);
  const windows=JSON.parse(matrix.match(/inputs.build_target == 'windows-reuse' && '([^']+)'/)[1]);
  assert.deepEqual(windows,[{os:'windows-latest',arch:'x64'}]);
  const macArm=JSON.parse(matrix.match(/inputs.build_target == 'mac-arm-reuse' && '([^']+)'/)[1]);
  assert.deepEqual(macArm,[{os:'macos-15',arch:'arm64','app-dir':'mac-arm64'}]);
  const macArmReuse=JSON.parse(desktop.jobs.reuse.strategy.matrix.artifact.match(/inputs.build_target == 'mac-arm-reuse' && '([^']+)'/)[1]);
  assert.deepEqual(macArmReuse,['desktop-Windows-x64','desktop-macOS-x64','desktop-Linux-x64','desktop-Linux-arm64']);
  assert.ok(desktop.jobs.reuse.strategy.matrix.artifact.includes('desktop-macOS-arm64'));
  const fullSuite=desktop.jobs.package.steps.find(step=>step.run==='python -B -m unittest discover -s tests -v');
  assert.equal(fullSuite.if,"inputs.build_target != 'intel'");
  assert.equal(matrix.match(/macos-15-intel/g).length,2);
  assert.ok(matrix.includes('macos-15')&&matrix.includes('windows-latest'));
});
test('mobile Intel supplement keeps the same checks and the default ARM build',()=>{
  const yaml=require('js-yaml'),load=name=>yaml.load(fs.readFileSync(path.join(__dirname,'../.github/workflows/'+name),'utf8'));
  const mobile=load('mobile.yml'),ci=load('tests.yml');
  assert.equal(mobile.on.workflow_dispatch.inputs.intel_fallback.default,false);
  assert.equal(mobile.jobs.ios['runs-on'],"${{ inputs.intel_fallback == true && 'macos-15-intel' || 'macos-15' }}");
  const supplement=mobile.jobs['mobile-update-tests'];
  assert.equal(supplement.if,'inputs.intel_fallback == true');assert.equal(supplement['runs-on'],'macos-15-intel');
  assert.deepEqual(supplement.steps.filter(step=>step.uses==='actions/setup-java@v4'||step.run?.startsWith('python3 scripts/')),
    ci.jobs['mobile-update-tests'].steps.filter(step=>step.uses==='actions/setup-java@v4'||step.run?.startsWith('python3 scripts/')));
  for(const job of [mobile.jobs.ios,supplement]){
    const validation=job.steps.find(step=>step.name==='Validate frozen Intel supplement source');assert.ok(validation);
    assert.ok(validation.run.includes("re.fullmatch(r'[0-9a-f]{40}', source)"));
    assert.ok(validation.run.includes("['git', 'rev-parse', 'HEAD']"));
    assert.ok(validation.run.includes("'2.2.2-preview.4'"));
    const checkout=job.steps.find(step=>step.uses==='actions/checkout@v4');
    assert.ok(checkout.with.ref.includes('inputs.source_ref'));
    assert.ok(job.steps.indexOf(validation)>job.steps.indexOf(checkout));
  }
  assert.ok(mobile.jobs.ios.steps.some(step=>step.run==='python scripts/build-mobile-ios.py'));
  assert.ok(mobile.jobs.ios.steps.some(step=>step.run?.includes('node --test mobile/tests/*.test.cjs')&&step.run.includes('test_mobile_ios_build.py')));
});
