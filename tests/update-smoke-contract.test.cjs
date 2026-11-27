'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),vm=require('node:vm');
const fs=require('node:fs/promises'),path=require('node:path'),{createHash}=require('node:crypto');
const production=require('../desktop/features/updates/updater.cjs');
const {stubUpdaterSource}=require('../scripts/smoke-update-controller.cjs');

async function fixture(t,current,target){
  const directory=await fs.mkdtemp(path.join(__dirname,'../.tmp/controller-contract-'));
  t.after(()=>fs.rm(directory,{recursive:true,force:true}));
  const archive=path.join(directory,'fixture.zip'),bytes=Buffer.from('controller contract archive');await fs.writeFile(archive,bytes);
  const module={exports:{}};
  vm.runInNewContext(stubUpdaterSource(target),{module,Buffer,Response,
    process:{platform:'win32',arch:'x64',env:{CMB_TEST_UPDATE_ARCHIVE:archive,CMB_TEST_UPDATE_SIZE:String(bytes.length),CMB_TEST_UPDATE_SHA256:createHash('sha256').update(bytes).digest('hex')}},
    require:name=>name==='./updater-production.cjs'?production:require(name)});
  let applied;
  const updater=new module.exports.Updater({current,platform:'win32',arch:'x64',directory:path.join(directory,'downloads'),install:async candidate=>{
    assert.equal(candidate.id,updater.status().candidateId);assert.equal(candidate.id,updater.candidate.id);
    assert.deepEqual(await fs.readFile(candidate.archive),bytes);applied=candidate;
  }});
  assert.ok(updater instanceof production.Updater);
  return {updater,archive,applied:()=>applied};
}
test('controller smoke uses a real checked candidate, immutable transition and verified package through production updater',async t=>{
  const f=await fixture(t,'2.2.2-preview.2','2.2.2-preview.3'),checked=await f.updater.check({manual:true,channel:'preview'});
  assert.equal(checked.state,'available',checked.message);assert.ok(checked.candidateId);assert.equal(checked.intent,'update');
  assert.equal(checked.requiresConfirmation,false);assert.equal(Object.isFrozen(f.updater.candidate),true);
  await assert.rejects(f.updater.install({candidateId:'wrong'}),/检查/);assert.equal(f.applied(),undefined);
  assert.equal((await f.updater.install({candidateId:checked.candidateId})).state,'restarting');
  assert.deepEqual(f.applied().transition,{intent:'update',from:'2.2.2-preview.2',to:'2.2.2-preview.3',dataFormat:'bridge-data-v1'});
});
test('controller smoke preserves explicit channel confirmation and invalidates a superseded checked identity',async t=>{
  for(const [current,target,channel,intent] of [['2.2.1','2.2.2-preview.2','preview','opt-in-preview'],['2.2.2-preview.2','2.2.1','stable','return-stable']]){
    const f=await fixture(t,current,target),checked=await f.updater.check({manual:true,channel});
    assert.equal(checked.state,'available',checked.message);assert.equal(checked.intent,intent);assert.equal(checked.requiresConfirmation,true);
    await assert.rejects(f.updater.install({candidateId:checked.candidateId}),/确认/);
    const again=await f.updater.check({manual:true,channel});assert.notEqual(again.candidateId,checked.candidateId);
    await assert.rejects(f.updater.install({candidateId:checked.candidateId,confirmed:true}),/检查/);
    assert.equal((await f.updater.install({candidateId:again.candidateId,confirmed:true})).state,'restarting');
    assert.equal(f.applied().transition.intent,intent);
  }
});
test('controller smoke cannot apply package bytes changed after its signed check',async t=>{
  const f=await fixture(t,'2.2.2-preview.2','2.2.2-preview.3'),checked=await f.updater.check();
  await fs.writeFile(f.archive,Buffer.from('corrupt'));
  assert.equal((await f.updater.install({candidateId:checked.candidateId})).state,'error');assert.equal(f.applied(),undefined);
});
