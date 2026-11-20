'use strict';
const test=require('node:test'),assert=require('node:assert/strict');
const fs=require('node:fs/promises'),path=require('node:path');
const {generateKeyPairSync,sign,createHash}=require('node:crypto');
const {buildManifest}=require('../scripts/sign-update.cjs');
const {Updater,manifest,compare,assetName,releaseUrl,allowedUrl,allowedMirrorUrl,mirrorUrl,RELEASES,MIRROR,transfer}=require('../desktop/features/updates/updater.cjs');
const keys=generateKeyPairSync('ed25519');
const current='0.2.0-beta.5',next='0.2.0-beta.6',platform='darwin',arch='arm64';
function signed(value,key=keys.privateKey){const payload=Buffer.from(JSON.stringify(value));return Buffer.from(JSON.stringify({payload:payload.toString('base64'),signature:sign(null,payload,key).toString('base64')}));}
const bytes=Buffer.from('fixture zip');
const info=()=>({schema:1,version:next,notes:'Notes <script> are text',assets:{'darwin-arm64':{name:assetName(next,platform,arch),size:bytes.length,sha256:createHash('sha256').update(bytes).digest('hex')}}});
const validate=value=>manifest(value,keys.publicKey,{current,platform,arch,expected:next});
test('release signer includes desktop packages and clients select their architecture and format',async t=>{
  await fs.mkdir(path.join(__dirname,'../.tmp'),{recursive:true});
  const directory=await fs.mkdtemp(path.join(__dirname,'../.tmp/release-manifest-'));
  t.after(()=>fs.rm(directory,{recursive:true,force:true}));
  const sync=require('node:fs'),read=sync.readFileSync;
  t.mock.method(sync,'readFileSync',(file,...args)=>file===path.resolve(__dirname,'../desktop/features/updates/update-public-key.pem')?
    keys.publicKey.export({type:'spki',format:'pem'}):read(file,...args));
  const version=require('../package.json').version,targets=[['darwin','arm64'],['darwin','x64'],['win32','x64'],...['x64','arm64'].flatMap(arch=>['deb','AppImage'].map(format=>['linux',arch,format]))];
  for(const [platform,arch,format] of targets)await fs.writeFile(path.join(directory,assetName(version,platform,arch,format)),Buffer.from(platform+'-'+arch+'-'+(format||'zip')));
  const signed=Buffer.from(buildManifest(directory,'Release notes',keys.privateKey));
  for(const [platform,arch,format] of targets){
    const result=manifest(signed,keys.publicKey,{current,platform,arch,format,expected:version});
    assert.equal(result.asset.name,assetName(version,platform,arch,format));
    assert.equal(result.asset.sha256,createHash('sha256').update(platform+'-'+arch+'-'+(format||'zip')).digest('hex'));
  }
  await fs.unlink(path.join(directory,assetName(version,'darwin','x64')));
  assert.throws(()=>buildManifest(directory,'Release notes',keys.privateKey),/ENOENT/);
});
test('release metadata requests JSON while downloadable assets request bytes',async()=>{
  for(const url of [RELEASES,releaseUrl(next,'bridge-update.json'),releaseUrl(next,assetName(next,platform,arch))]){
    const expected=url===RELEASES?'application/vnd.github+json':'application/octet-stream';
    const fetch=async(address,options)=>new Response('fixture',{status:options.headers.Accept===expected?200:415});
    assert.equal((await transfer(fetch,url,{limit:100})).bytes.toString(),'fixture');
  }
});
test('mirror transport is restricted and falls back without weakening verification',()=>{
  const manifestUrl=releaseUrl(next,'bridge-update.json');
  assert.equal(allowedMirrorUrl(RELEASES),RELEASES);
  assert.equal(allowedMirrorUrl(mirrorUrl(RELEASES)),MIRROR+RELEASES);
  for(const url of [MIRROR+'https://evil.example/update',MIRROR,MIRROR+'not-a-url'])assert.throws(()=>allowedMirrorUrl(url));
});

test('update metadata and packages retry through the signed transport mirror',async t=>{
  await fs.mkdir(path.join(__dirname,'../.tmp'),{recursive:true});
  const directory=await fs.mkdtemp(path.join(__dirname,'../.tmp/update-mirror-'));
  t.after(()=>fs.rm(directory,{recursive:true,force:true}));
  const requests=[];
  const official=url=>url.startsWith(MIRROR)?url.slice(MIRROR.length):url;
  const status=url=>url.startsWith(MIRROR)||!(url===RELEASES||url.endsWith('.zip'))?200:503;
  const response=(url,body)=>new Response(body,{status:status(url)});
  const fetch=async(url,options)=>{
    requests.push(url);
    return response(url,official(url)===RELEASES?JSON.stringify([{tag_name:'v'+next,assets:[{name:'bridge-update.json'}]}]):
      official(url).endsWith('.json')?signed(info()):bytes);
  };
  const updater=new Updater({current,platform,arch,key:keys.publicKey,fetch,directory,install:async()=>{}});
  assert.equal((await updater.check()).state,'available');
  assert.equal((await updater.install()).state,'restarting');
  assert.deepEqual(requests,[RELEASES,mirrorUrl(RELEASES),releaseUrl(next,'bridge-update.json'),
    updater.candidate.asset.url,mirrorUrl(updater.candidate.asset.url)]);
});

test('versions order beta, rc and stable numerically and reject ambiguous versions',()=>{
  assert.ok(compare('2.0.0-preview.10','2.0.0-preview.9')>0);
  assert.ok(compare('2.0.0','2.0.0-preview.99')>0);
  assert.ok(compare('0.2.0-beta.10','0.2.0-beta.9')>0);assert.ok(compare('0.2.0-rc.0','0.2.0-beta.99')>0);
  assert.ok(compare('0.2.0','0.2.0-rc.99')>0);assert.ok(compare('0.3.0-beta.0','0.2.99')>0);
  for(const bad of ['v0.2.0','0.2','0.2.0-beta','0.02.0','0.2.0-beta.01','1.0.0/../../'])assert.throws(()=>compare(bad,current));
});
test('v1.4.0 stable users never receive the v2 preview even with misleading release flags',async()=>{
  for(const version of ['2.0.0-preview.1','2.0.0-preview.2','2.0.0-preview.3'])for(const prerelease of [true,false]){
    let requests=0;
    const fetch=async()=>{requests++;return new Response(JSON.stringify([{tag_name:'v'+version,draft:false,prerelease,assets:[{name:'bridge-update.json'}]}]));};
    const updater=new Updater({current:'1.4.0',platform,arch,key:keys.publicKey,fetch,directory:'.tmp',install:async()=>{throw Error('Must not install');}});
    assert.equal((await updater.check()).state,'current');assert.equal(requests,1);assert.equal(updater.candidate,null);
  }
});
test('preview.2 discovers signed preview.3 only after it leaves draft',async()=>{
  const version='2.0.0-preview.3',data=info();data.version=version;data.assets['darwin-arm64'].name=assetName(version,platform,arch);
  for(const draft of [true,false]){
    const requests=[];
    const fetch=async url=>{requests.push(url);return new Response(url===RELEASES?
      JSON.stringify([{tag_name:'v'+version,draft,prerelease:true,assets:[{name:'bridge-update.json'}]}]):signed(data));};
    const updater=new Updater({current:'2.0.0-preview.2',platform,arch,key:keys.publicKey,fetch,directory:'.tmp',install:async()=>{throw Error('Must not install');}});
    const result=await updater.check();
    assert.equal(result.state,draft?'current':'available');
    assert.deepEqual(requests,draft?[RELEASES]:[RELEASES,releaseUrl(version,'bridge-update.json')]);
    assert.equal(updater.candidate?.version??null,draft?null:version);
  }
});
test('signatures authenticate exact payload and reject another release identity',()=>{
  assert.equal(validate(signed(info())).version,next);
  const envelope=JSON.parse(signed(info()));envelope.payload=Buffer.from(JSON.stringify({...info(),notes:'tampered'})).toString('base64');
  assert.throws(()=>validate(Buffer.from(JSON.stringify(envelope))),/签名/);
  assert.throws(()=>validate(signed(info(),generateKeyPairSync('ed25519').privateKey)),/签名/);
});
test('manifest rejects downgrades, wrong architecture, incomplete hashes and filename injection',()=>{
  const data=info();data.version=current;assert.throws(()=>validate(signed(data)),/版本/);
  for(const patch of [{name:'../../app.zip'},{size:0},{sha256:'123'}]){
    const value=info();Object.assign(value.assets['darwin-arm64'],patch);assert.throws(()=>validate(signed(value)),/校验/);
  }
  assert.throws(()=>manifest(signed(info()),keys.publicKey,{current,platform,arch:'x64',expected:next}),/校验/);
  assert.throws(()=>manifest(signed(info()),keys.publicKey,{current:'0.1.0',platform,arch,expected:next}),/版本/);
});
test('network allowlist rejects external destinations, credentials and non-HTTPS URLs',()=>{
  assert.equal(allowedUrl(RELEASES),RELEASES);
  assert.ok(allowedUrl(releaseUrl(next,'bridge-update.json')));
  for(const url of ['http://github.com/try2love/codex-mobile-bridge/releases/download/v1/x','https://evil.example/update','https://user@github.com/try2love/codex-mobile-bridge/releases/download/v1/x','https://github.com/other/repo/releases/download/v1/x'])assert.throws(()=>allowedUrl(url));
});
async function fixture(t,{packageBytes=bytes,releases,key=keys.publicKey,apply=async()=>{}}={}){
  await fs.mkdir(path.join(__dirname,'../.tmp'),{recursive:true});const directory=await fs.mkdtemp(path.join(__dirname,'../.tmp/update-test-'));
  t.after(()=>fs.rm(directory,{recursive:true,force:true}));const requests=[];let installed=0;
  const fetch=async url=>{requests.push(url);return new Response(url===RELEASES?JSON.stringify(releases||[{tag_name:'v'+next,assets:[{name:'bridge-update.json'}]}]):url.endsWith('.json')?signed(info()):packageBytes);};
  const updater=new Updater({current,platform,arch,key,fetch,directory,install:async value=>{installed++;assert.equal(await fs.readFile(value.archive,'utf8'),bytes.toString());await apply(value);}});
  return {updater,requests,directory,installed:()=>installed};
}
test('checking never installs and installation only receives verified bytes',async t=>{
  const f=await fixture(t);assert.equal((await f.updater.check()).state,'available');assert.equal(f.installed(),0);
  assert.equal((await f.updater.install()).state,'restarting');assert.equal(f.installed(),1);assert.deepEqual(await fs.readdir(f.directory),[]);
});
test('a live local holder blocks installation before package download and permits retry after exit',async t=>{
  const f=await fixture(t);await f.updater.check();
  f.updater.preflight=async()=>{throw Error('请先结束任务并正常退出 ZCode，再更新网关。');};
  const requests=f.requests.length;
  assert.equal((await f.updater.install()).state,'error');
  assert.equal(f.requests.length,requests);assert.equal(f.installed(),0);
  assert.deepEqual(await fs.readdir(f.directory),[]);
  f.updater.preflight=async()=>{};
  assert.equal((await f.updater.install()).state,'restarting');assert.equal(f.installed(),1);
});
test('skipping an exact signed release survives restart while manual checks and newer releases remain available',async t=>{
  const f=await fixture(t),skipFile=path.join(f.directory,'skipped-update.json');f.updater.skipFile=skipFile;
  assert.equal((await f.updater.check()).state,'available');assert.equal((await f.updater.skip()).state,'skipped');
  assert.deepEqual(JSON.parse(await fs.readFile(skipFile,'utf8')),{[f.updater.skipIdentity]:next});
  const again=new Updater({current,platform,arch,key:keys.publicKey,fetch:f.updater.fetch,directory:f.directory,skipFile,install:async()=>{}});
  assert.equal((await again.check()).state,'skipped');assert.equal(again.candidate.version,next);
  assert.equal((await again.check({manual:true})).state,'available');assert.equal(again.status().skipped,true);
  const newer='0.2.0-beta.7',release=info();release.version=newer;release.assets['darwin-arm64'].name=assetName(newer,platform,arch);
  again.fetch=async url=>new Response(url===RELEASES?JSON.stringify([{tag_name:'v'+newer,assets:[{name:'bridge-update.json'}]}]):signed(release));
  assert.equal((await again.check()).state,'available');assert.equal(again.status().skipped,undefined);
  assert.equal(f.installed(),0);
  const otherChannel=new Updater({current:'1.0.0',platform,arch,key:keys.publicKey,fetch:again.fetch,directory:f.directory,skipFile,install:async()=>{}});
  assert.equal(await otherChannel.skippedVersion(),'');
});
test('corrupt and truncated downloads cannot stop the gateway or invoke installation',async t=>{
  for(const packageBytes of [Buffer.from('fixture bad'),Buffer.from('short'),Buffer.alloc(100)]){
    const f=await fixture(t,{packageBytes});await f.updater.check();assert.equal((await f.updater.install()).state,'error');assert.equal(f.installed(),0);assert.deepEqual(await fs.readdir(f.directory),[]);
  }
});
test('concurrent update clicks cause a single installation; checking cannot replace an active update',async t=>{
  let done;const wait=new Promise(resolve=>{done=resolve;});const f=await fixture(t,{apply:()=>wait});await f.updater.check();
  const pending=f.updater.install();await new Promise(setImmediate);await f.updater.install();await f.updater.check();done();await pending;assert.equal(f.installed(),1);
});
test('unpublished/draft releases are excluded and stable users stay on stable',async t=>{
  const f=await fixture(t,{releases:[{tag_name:'v99.0.0',draft:true,assets:[{name:'bridge-update.json'}]},{tag_name:'v98.0.0',assets:[]}]});
  assert.equal((await f.updater.check()).state,'current');assert.equal(f.requests.length,1);
  const stable=new Updater({current:'0.1.0',platform,arch,key:keys.publicKey,directory:f.directory,install:async()=>{},
    fetch:async()=>new Response(JSON.stringify([{tag_name:'v'+next,prerelease:true,assets:[{name:'bridge-update.json'}]}]))});
  assert.equal((await stable.check()).state,'current');
});
test('a signature failure leaves no installable candidate',async t=>{
  const f=await fixture(t,{key:generateKeyPairSync('ed25519').publicKey});assert.equal((await f.updater.check()).state,'error');await assert.rejects(f.updater.install(),/检查/);assert.equal(f.installed(),0);
});
test('failed preparation reports an error and preserves the candidate for retry',async t=>{
  let tries=0;const f=await fixture(t,{apply:async()=>{if(!tries++)throw Error('unwritable directory');}});await f.updater.check();
  assert.equal((await f.updater.install()).message,'unwritable directory');assert.equal((await f.updater.install()).state,'restarting');
});

async function channelFixture(t,{current='2.2.1',versions=['2.2.2-preview.2','2.2.1'],metadata={},fetchRelease}={}){
  const f=await fixture(t);
  f.updater=new Updater({current,platform,arch,key:keys.publicKey,directory:f.directory,install:async value=>{
    assert.equal(await fs.readFile(value.archive,'utf8'),bytes.toString());f.applied=value;
  },fetch:async url=>{
    f.requests.push(url);
    if(url===RELEASES)return new Response(JSON.stringify(versions.map(v=>({tag_name:'v'+v,prerelease:v.includes('-'),assets:[{name:'bridge-update.json'}]}))));
    if(!url.endsWith('.json'))return new Response(bytes);
    await fetchRelease?.(url);
    const v=/\/v([^/]+)\//.exec(url)[1],data=info();data.version=v;data.assets['darwin-arm64'].name=assetName(v,platform,arch);
    return new Response(signed({...data,...metadata}));
  }});
  return f;
}
test('stable preview selection only checks, requires exact candidate confirmation, and never changes background channel',async t=>{
  const f=await channelFixture(t);
  assert.equal(f.updater.status().channel,'stable');
  assert.equal((await f.updater.check()).state,'current');
  const result=await f.updater.check({manual:true,channel:'preview'});
  assert.equal(result.state,'available');assert.equal(result.intent,'opt-in-preview');assert.equal(result.requiresConfirmation,true);
  assert.equal(f.applied,undefined);assert.equal(result.channel,'preview');assert.equal(result.currentChannel,'stable');
  await assert.rejects(f.updater.install(),/确认/);
  await assert.rejects(f.updater.install({candidateId:'stale',confirmed:true}),/检查/);
  assert.equal((await f.updater.check()).candidateId,result.candidateId);
  await f.updater.install({candidateId:result.candidateId,confirmed:true});
  assert.equal(f.applied.version,'2.2.2-preview.2');
});
test('preview returns to the signed latest stable only by explicit checked and confirmed intent',async t=>{
  const f=await channelFixture(t,{current:'2.2.2-preview.2'});
  assert.equal(f.updater.status().channel,'preview');assert.equal((await f.updater.check()).state,'current');
  const result=await f.updater.check({manual:true,channel:'stable'});
  assert.equal(result.version,'2.2.1');assert.equal(result.intent,'return-stable');assert.equal(result.requiresConfirmation,true);
  assert.equal(Object.isFrozen(f.updater.candidate),true);assert.equal(Object.isFrozen(f.updater.candidate.asset),true);
  await assert.rejects(f.updater.install({candidateId:result.candidateId}),/确认/);
  await f.updater.install({candidateId:result.candidateId,confirmed:true});
  assert.equal(f.applied.transition.intent,'return-stable');assert.equal(f.applied.transition.from,'2.2.2-preview.2');
});
test('return stable blocks unknown historical data compatibility and default manifest validation still rejects a downgrade',async t=>{
  const f=await channelFixture(t,{current:'2.2.2-preview.2',versions:['2.2.0']});
  assert.equal((await f.updater.check({manual:true,channel:'stable'})).state,'error');assert.equal(f.updater.candidate,null);
  const data=info();data.version='2.2.1';data.assets['darwin-arm64'].name=assetName(data.version,platform,arch);
  assert.throws(()=>manifest(signed(data),keys.publicKey,{current:'2.2.2-preview.2',platform,arch,expected:'2.2.1'}),/版本/);
});
test('switching channel invalidates an in-flight result and the old candidate cannot install',async t=>{
  let release;const wait=new Promise(resolve=>{release=resolve;});let reads=0;
  const f=await channelFixture(t,{fetchRelease:async()=>{if(!reads++)await wait;}});
  const pending=f.updater.check({manual:true,channel:'preview'});await new Promise(setImmediate);
  assert.equal((await f.updater.check({manual:true,channel:'stable'})).state,'current');
  release();await pending;assert.equal(f.updater.status().channel,'stable');assert.equal(f.updater.candidate,null);
  await assert.rejects(f.updater.install({candidateId:'old',confirmed:true}),/检查/);
});
test('new checks invalidate confirmation identities and skip records are scoped to the selected channel',async t=>{
  const f=await channelFixture(t,{current:'2.2.0',versions:['2.2.2-preview.2','2.2.1']});
  const old=await f.updater.check({manual:true,channel:'preview'});await f.updater.skip();
  assert.equal(await f.updater.skippedVersion(),'2.2.2-preview.2');
  assert.equal((await f.updater.check({manual:true,channel:'stable'})).version,'2.2.1');assert.equal(await f.updater.skippedVersion(),'');
  const latest=await f.updater.check({manual:true,channel:'preview'});assert.notEqual(latest.candidateId,old.candidateId);
  await assert.rejects(f.updater.install({candidateId:old.candidateId,confirmed:true}),/检查/);
});
test('preview automatic checks still promote to a newer stable release',async t=>{
  const f=await channelFixture(t,{current:'2.2.2-preview.2',versions:['2.2.2','2.2.2-preview.3']});
  const result=await f.updater.check();assert.equal(result.version,'2.2.2');assert.equal(result.intent,'update');assert.equal(result.requiresConfirmation,false);
});
test('skip persistence cannot race installation or replace the selected channel',async t=>{
  const f=await channelFixture(t);await f.updater.check({manual:true,channel:'preview'});
  let release;f.updater.skippedVersions=()=>new Promise(resolve=>{release=resolve;});
  const skip=f.updater.skip();await assert.rejects(f.updater.install({candidateId:f.updater.status().candidateId,confirmed:true}),/检查/);
  assert.equal((await f.updater.check({manual:true,channel:'stable'})).channel,'preview');
  release({});await skip;assert.equal(f.updater.status().state,'skipped');
});
test('Linux signed packages select the installed format and retain verified downloads without claiming a restart',async t=>{
  for(const arch of ['x64','arm64'])for(const format of ['deb','AppImage']){
    const f=await fixture(t),name=assetName('2.2.2-preview.2','linux',arch,format);
    const data={...info(),version:'2.2.2-preview.2',dataFormat:'bridge-data-v1',assets:{[`linux-${arch}-${format}`]:{name,size:bytes.length,sha256:createHash('sha256').update(bytes).digest('hex')}}};
    let applied;
    const updater=new Updater({current:'2.2.1',platform:'linux',arch,format,key:keys.publicKey,directory:f.directory,
      fetch:async url=>new Response(url===RELEASES?JSON.stringify([{tag_name:'v2.2.2-preview.2',prerelease:true,assets:[{name:'bridge-update.json'}]}]):url.endsWith('.json')?signed(data):bytes),
      install:async value=>{applied=value;return {state:format==='deb'?'installer-handoff':'downloaded'};}});
    const checked=await updater.check({manual:true,channel:'preview'});assert.equal(checked.manualInstall,true);
    const result=await updater.install({candidateId:checked.candidateId,confirmed:true});
    assert.equal(result.state,format==='deb'?'installer-handoff':'downloaded');assert.equal(path.basename(result.path),name);
    assert.equal(await fs.readFile(applied.archive,'utf8'),bytes.toString());assert.equal(updater.busy,false);
  }
});
test('Linux corrupt signed package never reaches installer and a legacy unsigned asset only offers official release page',async t=>{
  const f=await fixture(t),name=assetName('2.2.2-preview.2','linux','x64');let applied=0;
  const data={...info(),version:'2.2.2-preview.2',assets:{'linux-x64-deb':{name,size:bytes.length,sha256:createHash('sha256').update(bytes).digest('hex')}}};
  const updater=new Updater({current:'2.2.1',platform:'linux',arch:'x64',key:keys.publicKey,directory:f.directory,
    fetch:async url=>new Response(url===RELEASES?JSON.stringify([{tag_name:'v2.2.2-preview.2',prerelease:true,assets:[{name:'bridge-update.json'}]}]):url.endsWith('.json')?signed(data):Buffer.from('wrong bytes')),
    install:async()=>{applied++;}});
  const checked=await updater.check({manual:true,channel:'preview'});assert.equal((await updater.install({candidateId:checked.candidateId,confirmed:true})).state,'error');assert.equal(applied,0);assert.deepEqual(await fs.readdir(f.directory),[]);
  const legacy=manifest(signed({...info(),version:'2.2.1'}),keys.publicKey,{current:'2.2.2-preview.2',platform:'linux',arch:'x64',expected:'2.2.1',channel:'stable',intent:'return-stable'});
  assert.equal(legacy.manualRelease,true);assert.equal(legacy.asset,undefined);assert.equal(legacy.releasePage,'https://github.com/try2love/codex-mobile-bridge/releases/tag/v2.2.1');
});
