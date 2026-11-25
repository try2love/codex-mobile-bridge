'use strict';
const test=require('node:test'),assert=require('node:assert/strict');
const fs=require('node:fs/promises'),path=require('node:path');
const {generateKeyPairSync,sign,createHash}=require('node:crypto');
const {buildManifest}=require('../scripts/sign-update.cjs');
const {Updater,manifest,compare,assetName,cacheName,releaseUrl,allowedUrl,allowedCatalogueUrl,allowedMirrorUrl,mirrorUrl,RELEASES,MIRROR,transfer}=require('../desktop/features/updates/updater.cjs');
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
async function fixture(t,{packageBytes=bytes,releases,key=keys.publicKey,apply=async()=>{},platform:targetPlatform=platform,arch:targetArch=arch}={}){
  await fs.mkdir(path.join(__dirname,'../.tmp'),{recursive:true});const directory=await fs.mkdtemp(path.join(__dirname,'../.tmp/update-test-'));
  t.after(()=>fs.rm(directory,{recursive:true,force:true}));const requests=[];let installed=0;
  const data=info();data.assets={[`${targetPlatform}-${targetArch}`]:{...data.assets[`${platform}-${arch}`],name:assetName(next,targetPlatform,targetArch)}};
  const fetch=async url=>{requests.push(url);return new Response(url===RELEASES?JSON.stringify(releases||[{tag_name:'v'+next,assets:[{name:'bridge-update.json'}]}]):url.endsWith('.json')?signed(data):packageBytes);};
  const updater=new Updater({current,platform:targetPlatform,arch:targetArch,key,fetch,directory,install:async value=>{installed++;assert.equal(await fs.readFile(value.archive,'utf8'),bytes.toString());await apply(value);}});
  return {updater,requests,directory,installed:()=>installed};
}
test('checking never installs and installation only receives verified bytes',async t=>{
  const f=await fixture(t);assert.equal((await f.updater.check()).state,'available');assert.equal(f.installed(),0);
  assert.equal((await f.updater.install()).state,'restarting');assert.equal(f.installed(),1);
  assert.equal((await fs.readdir(f.directory)).filter(name=>name.startsWith('verified-')).length,1);
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

for(const [targetPlatform,targetArch] of [['darwin','arm64'],['win32','x64']])test(`${targetPlatform} reuses a verified package after a failed install instead of downloading again`,async t=>{
  let attempts=0,tries=0;const f=await fixture(t,{platform:targetPlatform,arch:targetArch,apply:async()=>{if(!tries++)throw Error('installer failed');}});
  await f.updater.check();
  assert.equal((await f.updater.install()).state,'error');
  assert.equal((await f.updater.install()).state,'restarting');
  attempts=f.requests.filter(url=>url.endsWith('.zip')).length;
  assert.equal(attempts,1);
  assert.equal((await fs.readdir(f.directory)).filter(name=>name.startsWith('verified-')).length,1);
});

test('a package survives a failed post-handoff update and successful restart for reuse',async t=>{
  const f=await fixture(t);
  await f.updater.check();
  assert.equal((await f.updater.install()).state,'restarting');
  const retry=new Updater({current,platform,arch,key:keys.publicKey,fetch:f.updater.fetch,directory:f.directory,install:async value=>{
    assert.equal(await fs.readFile(value.archive,'utf8'),bytes.toString());
  }});
  await retry.check();
  assert.equal((await retry.install()).state,'restarting');
  assert.equal(f.requests.filter(url=>url.endsWith('.zip')).length,1);
  assert.equal((await fs.readdir(f.directory)).filter(name=>name.startsWith('verified-')).length,1);
  const restarted=new Updater({current:next,platform,arch,key:keys.publicKey,directory:f.directory,install:async()=>{}});
  assert.equal(restarted.cacheStatus().entries.length,1);
  assert.equal(restarted.cacheStatus().entries[0].version,next);
});

test('a changed cached package fails its signed digest and is downloaded again',async t=>{
  let tries=0;const f=await fixture(t,{apply:async()=>{if(!tries++)throw Error('installer failed');}});
  await f.updater.check();await f.updater.install();
  const verified=(await fs.readdir(f.directory)).find(name=>name.startsWith('verified-'));
  assert.ok(verified);
  await fs.writeFile(path.join(f.directory,verified),'modified');
  await f.updater.install();
  assert.equal(f.requests.filter(url=>url.endsWith('.zip')).length,2);
  assert.equal((await fs.readdir(f.directory)).filter(name=>name.startsWith('verified-')).length,1);
});

test('startup cleanup retains installed archives and upgrade and downgrade retries',async t=>{
  const f=await fixture(t),installed=path.join(f.directory,'verified-0.2.0-beta.5-darwin-arm64-'+'a'.repeat(64)+'.zip');
  const older=path.join(f.directory,'verified-0.1.0-darwin-arm64-'+'c'.repeat(64)+'.zip');
  const newer=path.join(f.directory,'verified-0.2.0-beta.7-darwin-arm64-'+'b'.repeat(64)+'.zip');
  await fs.writeFile(installed,'installed');await fs.writeFile(newer,'retry');await fs.writeFile(older,'return-stable');
  const {cleanDownloadStaging}=require('../desktop/features/updates/updater.cjs');await cleanDownloadStaging(f.directory);
  assert.equal(await fs.readFile(installed,'utf8'),'installed');
  assert.equal(await fs.readFile(newer,'utf8'),'retry');
  assert.equal(await fs.readFile(older,'utf8'),'return-stable');
});

test('stale download staging is removed without touching verified package cache',async t=>{
  const f=await fixture(t);await fs.mkdir(path.join(f.directory,'download-stale'));await fs.writeFile(path.join(f.directory,'download-stale','update.zip'),'partial');
  const verified=path.join(f.directory,'verified-keep.zip');await fs.writeFile(verified,'keep');
  const {cleanDownloadStaging}=require('../desktop/features/updates/updater.cjs');await cleanDownloadStaging(f.directory);
  assert.equal(await fs.access(verified).then(()=>true).catch(()=>false),true);
  assert.equal(await fs.access(path.join(f.directory,'download-stale')).then(()=>true).catch(()=>false),false);
});

function cachedName(version='2.2.1',platform='darwin',arch='arm64',format='zip'){
  return cacheName({version,asset:{name:assetName(version,platform,arch,format),sha256:'a'.repeat(64)}},platform,arch,format);
}
test('cache listing reports supported package metadata without treating filenames as verified bytes',async t=>{
  const f=await fixture(t),targets=[['darwin','arm64','zip'],['darwin','x64','zip'],['darwin','arm64','dmg'],['darwin','x64','dmg'],['win32','x64','zip'],['win32','x64','exe'],
    ['linux','x64','deb'],['linux','arm64','deb'],['linux','x64','AppImage'],['linux','arm64','AppImage']];
  let total=0;
  for(const [index,[platform,arch,format]] of targets.entries()){
    const name=cachedName('2.2.2-preview.3',platform,arch,format),file=path.join(f.directory,name),body='x'.repeat(index+1);
    await fs.writeFile(file,body);await fs.utimes(file,1000+index,1000+index);total+=body.length;
  }
  const ignored=[cachedName().replace('2.2.1','02.2.1'),cachedName('2.2.1','win32','x64').replace('-x64-','-arm64-'),
    cachedName('2.2.1','win32','x64').replace('.zip','.dmg'),cachedName().replace('.zip','.exe'),'accounts.json','verified-keep.zip'];
  for(const name of ignored)await fs.writeFile(path.join(f.directory,name),'ignore');
  const directory=cachedName('1.0.0');await fs.mkdir(path.join(f.directory,directory));
  const link=cachedName('1.0.1');await fs.symlink(path.join(f.directory,ignored[0]),path.join(f.directory,link));
  const listed=f.updater.cacheStatus();assert.equal(listed.totalBytes,total);assert.equal(listed.entries.length,targets.length);
  for(const entry of listed.entries){
    const index=targets.findIndex(([platform,arch,format])=>entry.platform===platform&&entry.arch===arch&&entry.format===format);
    assert.equal(entry.version,'2.2.2-preview.3');assert.equal(entry.size,index+1);assert.equal(entry.modifiedAt,(1000+index)*1000);assert.equal(entry.inUse,false);
    assert.equal(entry.path,undefined);assert.equal(entry.sha256,undefined);
  }
  assert.ok(f.updater.cacheStatus({inUse:true}).entries.every(entry=>entry.inUse));
  f.updater.set('preparing');assert.ok(f.updater.cacheStatus().entries.every(entry=>entry.inUse));
});
test('cache deletion only unlinks the selected regular package and preserves other data',async t=>{
  const f=await fixture(t),selected=cachedName(),retained=cachedName('2.2.2-preview.3');
  for(const name of [selected,retained,'accounts.json','config.json','bindings.json','drafts.json'])await fs.writeFile(path.join(f.directory,name),name);
  await fs.mkdir(path.join(f.directory,'backups'));await fs.writeFile(path.join(f.directory,'backups','snapshot.zip'),'backup');
  const selection=f.updater.cacheSelection([selected,selected]);assert.equal(selection.files.length,1);
  assert.deepEqual(f.updater.deleteCache(selection),[selected]);
  assert.equal(f.updater.cacheStatus().entries[0].id,retained);
  for(const name of [retained,'accounts.json','config.json','bindings.json','drafts.json','backups/snapshot.zip'])assert.equal(await fs.readFile(path.join(f.directory,name),'utf8'),name==='backups/snapshot.zip'?'backup':name);
});
test('cache selections reject traversal, unknown names, links and directories',async t=>{
  const f=await fixture(t),name=cachedName();await fs.writeFile(path.join(f.directory,name),'package');
  for(const ids of [undefined,[],[null],['../'+name],[path.join(f.directory,name)],['nested/'+name],['..\\'+name],['accounts.json'],[name+'/..'],[name+'\0']])
    assert.throws(()=>f.updater.cacheSelection(ids),/请求无效/);
  await fs.unlink(path.join(f.directory,name));await fs.symlink(path.join(f.directory,'accounts.json'),path.join(f.directory,name));
  assert.throws(()=>f.updater.cacheSelection([name]),/已变化/);
  await fs.unlink(path.join(f.directory,name));await fs.mkdir(path.join(f.directory,name));
  assert.throws(()=>f.updater.cacheSelection([name]),/已变化/);
});
for(const replacement of ['modified','symlink','directory'])test(`cache deletion rejects ${replacement} replacements after selection before deleting any file`,async t=>{
  const f=await fixture(t),names=[cachedName(),cachedName('2.2.2-preview.3')];
  for(const name of names)await fs.writeFile(path.join(f.directory,name),'package');
  const selected=f.updater.cacheSelection(names),changed=path.join(f.directory,names[1]);
  if(replacement==='modified')await fs.writeFile(changed,'tampered package');
  else{
    await fs.unlink(changed);
    if(replacement==='symlink')await fs.symlink(path.join(f.directory,names[0]),changed);else await fs.mkdir(changed);
  }
  assert.throws(()=>f.updater.deleteCache(selected),/已变化/);assert.equal(await fs.readFile(path.join(f.directory,names[0]),'utf8'),'package');
});
test('cache deletion rejects a replaced or linked cache directory and any active installation',async t=>{
  const f=await fixture(t),name=cachedName();await fs.writeFile(path.join(f.directory,name),'package');
  const selected=f.updater.cacheSelection([name]);f.updater.set('downloading');
  assert.throws(()=>f.updater.deleteCache(selected),/更新完成/);f.updater.set('idle');
  const moved=f.directory+'-moved';t.after(()=>fs.rm(moved,{recursive:true,force:true}));
  await fs.rename(f.directory,moved);await fs.symlink(moved,f.directory);
  assert.throws(()=>f.updater.cacheStatus(),/已变化/);assert.throws(()=>f.updater.deleteCache(selected),/已变化/);
  assert.equal(await fs.readFile(path.join(moved,name),'utf8'),'package');
  await fs.unlink(f.directory);await fs.mkdir(f.directory);await fs.writeFile(path.join(f.directory,name),'new package');
  assert.throws(()=>f.updater.deleteCache(selected),/已变化/);assert.equal(await fs.readFile(path.join(f.directory,name),'utf8'),'new package');
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
test('explicit stable selection offers a newer stable with return intent and exact-candidate confirmation',async t=>{
  const f=await channelFixture(t,{current:'2.2.2-preview.2',versions:['2.2.3-preview.1','2.2.2']});
  const result=await f.updater.check({manual:true,channel:'stable'});
  assert.equal(result.channel,'stable');assert.equal(result.currentChannel,'preview');assert.equal(result.version,'2.2.2');
  assert.equal(result.intent,'return-stable');assert.equal(result.requiresConfirmation,true);
  await assert.rejects(f.updater.install({candidateId:result.candidateId}),/确认/);
  await f.updater.install({candidateId:result.candidateId,confirmed:true});assert.equal(f.applied.transition.intent,'return-stable');
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
    assert.equal(result.state,format==='deb'?'installer-handoff':'downloaded');assert.ok(result.path.endsWith(name));
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

function historyRelease(target,{platform='darwin',arch='arm64',formats=platform==='linux'?['deb','AppImage']:platform==='darwin'?['zip','dmg']:['zip','exe'],hasManifest=true}={}){
  const hash=createHash('sha256').update(bytes).digest('hex');
  return {tag_name:'v'+target,draft:false,prerelease:target.includes('-'),published_at:'2026-11-21T00:00:00Z',body:'Release '+target,
    assets:[...formats.map(format=>({name:assetName(target,platform,arch,format),size:bytes.length,digest:'sha256:'+hash,
      browser_download_url:releaseUrl(target,assetName(target,platform,arch,format))})),
      ...(hasManifest?[{name:'bridge-update.json',browser_download_url:releaseUrl(target,'bridge-update.json')}]:[])]};
}
async function historyFixture(t,{current='2.2.2',platform='darwin',arch='arm64',rows,metadata={},packageBytes=bytes,manifestWait,install}={}){
  const base=await fixture(t),requests=[],applied=[];
  rows=rows||['2.2.2','2.2.2-preview.3','2.2.1'].map(v=>historyRelease(v,{platform,arch}));
  const fetch=async(url,options)=>{
    requests.push({url,options});
    if(url.startsWith(RELEASES+'&page=')){
      const page=Number(url.slice((RELEASES+'&page=').length));return new Response(JSON.stringify(rows.slice((page-1)*100,page*100)));
    }
    if(url===RELEASES)return new Response(JSON.stringify(rows));
    if(url.endsWith('bridge-update.json')){
      const target=/\/v([^/]+)\//.exec(url)[1];await manifestWait?.(target);
      const assets={};
      for(const format of platform==='linux'?['deb','AppImage']:['zip'])assets[`${platform}-${arch}${platform==='linux'?'-'+format:''}`]={
        name:assetName(target,platform,arch,format),size:bytes.length,sha256:createHash('sha256').update(bytes).digest('hex')};
      const data={schema:1,version:target,dataFormat:'bridge-data-v1',notes:'Signed '+target,assets,...metadata};
      return new Response(signed(data));
    }
    return new Response(packageBytes);
  };
  const updater=new Updater({current,platform,arch,key:keys.publicKey,directory:base.directory,fetch,catalogueFetch:fetch,
    install:async candidate=>{applied.push(candidate);return install?.(candidate);}});
  return {updater,rows,requests,applied,directory:base.directory};
}
test('history enumerates all pages and only local platform formats from the official catalogue',async t=>{
  for(const [platform,arch,formats] of [['darwin','arm64',['zip','dmg']],['darwin','x64',['zip','dmg']],['win32','x64',['zip','exe']],['linux','x64',['deb','AppImage']],['linux','arm64',['deb','AppImage']]]){
    const rows=Array.from({length:103},(_,index)=>historyRelease('2.1.'+index,{platform,arch}));
    rows[0].draft=true;rows[1].assets.push(...historyRelease('2.1.1',{platform:platform==='linux'?'darwin':'linux',arch}).assets.filter(item=>item.name!=='bridge-update.json'));
    const f=await historyFixture(t,{platform,arch,rows}),result=await f.updater.listVersions();
    assert.equal(result.current,'2.2.2');assert.equal(result.platform,platform);assert.equal(result.arch,arch);assert.equal(result.releases.length,102);
    assert.equal(result.releases[0].version,'2.1.102');assert.equal(result.releases.at(-1).version,'2.1.1');
    for(const row of result.releases){assert.deepEqual(row.assets.map(asset=>asset.format),formats);assert.equal(row.publishedAt,'2026-11-21T00:00:00Z');
      assert.equal(row.channel,'stable');assert.ok(row.assets.every(asset=>asset.size===bytes.length&&asset.id&&asset.path===undefined&&asset.url===undefined));}
    assert.deepEqual(f.requests.map(request=>request.url),[RELEASES+'&page=1',RELEASES+'&page=2']);
    assert.ok(f.requests.every(request=>request.options.headers.Accept==='application/vnd.github+json'));
  }
});
test('catalogue pagination is exact and never falls back to mirror or silently keeps partial results',async t=>{
  for(const url of [RELEASES+'&page=1',RELEASES+'&page=987'])assert.equal(allowedCatalogueUrl(url),url);
  for(const url of [RELEASES,MIRROR+RELEASES+'&page=1',RELEASES+'&page=0',RELEASES+'&page=01',RELEASES+'&page=1&other=yes',RELEASES+'&page=1#x',RELEASES.replace('https:','http:')+'&page=1'])assert.throws(()=>allowedCatalogueUrl(url),/来源/);
  const f=await historyFixture(t);let calls=0;
  f.updater.catalogueFetch=async url=>{calls++;return new Response('unavailable',{status:503});};
  await assert.rejects(f.updater.listVersions(),/无法获取/);assert.equal(calls,1);assert.equal(f.updater.catalogueAssets.size,0);
  const rows=Array.from({length:100},(_,index)=>historyRelease('1.0.'+index));
  f.updater.catalogueFetch=async url=>new Response(url.endsWith('page=1')?JSON.stringify(rows):'{invalid');
  await assert.rejects(f.updater.listVersions());assert.equal(f.updater.catalogueAssets.size,0);
  f.updater.catalogueFetch=async()=>({ok:true,status:200,body:new Response('[]').body,url:'https://evil.example/releases'});
  await assert.rejects(f.updater.listVersions(),/来源/);
  const exact=await historyFixture(t,{rows});assert.equal((await exact.updater.listVersions()).releases.length,100);
  assert.deepEqual(exact.requests.map(request=>request.url),[RELEASES+'&page=1',RELEASES+'&page=2']);
  const duplicate=await historyFixture(t,{rows:[...rows,rows[0]]});await assert.rejects(duplicate.updater.listVersions(),/列表无效/);
});
test('history rejects invalid official asset metadata while ignoring other products and invalid tags',async t=>{
  for(const patch of [{digest:undefined},{digest:'sha256:'+'z'.repeat(64)},{size:0},{size:2**31},{browser_download_url:'https://evil.example/package.zip'},
    {browser_download_url:releaseUrl('2.2.1',assetName('2.2.2',platform,arch))}]){
    const row=historyRelease('2.2.2');Object.assign(row.assets[0],patch);const f=await historyFixture(t,{rows:[row]});
    await assert.rejects(f.updater.listVersions(),/校验|来源/);assert.equal(f.updater.catalogueAssets.size,0);
  }
  const f=await historyFixture(t,{rows:[{...historyRelease('2.2.2'),tag_name:'v02.2.2'},
    {...historyRelease('2.2.2'),assets:[{name:'../../malicious.zip'}]},historyRelease('2.2.1')]});
  assert.deepEqual((await f.updater.listVersions()).releases.map(row=>row.version),['2.2.1']);
});
test('explicit history selection switches stable to preview and back, including same-version reinstall',async t=>{
  for(const [current,target] of [['2.2.2','2.2.2-preview.3'],['2.2.2-preview.3','2.2.2'],['2.2.2','2.2.2'],['2.2.2','2.2.1']]){
    const f=await historyFixture(t,{current}),list=await f.updater.listVersions(),asset=list.releases.find(row=>row.version===target).assets.find(asset=>asset.format==='zip');
    const checked=await f.updater.selectVersion(asset.id);
    assert.equal(checked.state,'available');assert.equal(checked.version,target);assert.equal(checked.intent,'switch-version');assert.equal(checked.selected,true);
    assert.equal(checked.manualInstall,false);assert.equal(checked.format,'zip');assert.equal(checked.assetName,asset.name);assert.equal(checked.requiresConfirmation,true);
    assert.equal(f.applied.length,0);assert.ok(Object.isFrozen(f.updater.candidate)&&Object.isFrozen(f.updater.candidate.asset)&&Object.isFrozen(f.updater.candidate.transition));
    await assert.rejects(f.updater.install({candidateId:checked.candidateId}),/确认/);
    assert.equal((await f.updater.install({candidateId:checked.candidateId,confirmed:true})).state,'restarting');
    assert.deepEqual(f.applied[0].transition,{intent:'switch-version',from:current,to:target,dataFormat:'bridge-data-v1'});
  }
});
test('history download verifies once without preflight or apply and installation reuses the cache after full revalidation',async t=>{
  const f=await historyFixture(t),list=await f.updater.listVersions(),checked=await f.updater.selectVersion(list.releases[0].assets[0].id);let preflight=0;
  f.updater.preflight=async()=>{preflight++;};
  const downloaded=await f.updater.download({candidateId:checked.candidateId});assert.equal(downloaded.state,'downloaded');assert.equal(downloaded.selected,true);
  assert.equal(downloaded.candidateId,checked.candidateId);assert.equal(downloaded.assetName,checked.assetName);assert.equal(f.applied.length,0);assert.equal(preflight,0);
  const packageRequests=()=>f.requests.filter(request=>request.url.endsWith('.zip')).length;assert.equal(packageRequests(),1);
  await f.updater.download({candidateId:checked.candidateId});assert.equal(packageRequests(),1);
  await fs.writeFile(downloaded.path,'tampered package');await f.updater.download({candidateId:checked.candidateId});assert.equal(packageRequests(),2);
  await f.updater.install({candidateId:checked.candidateId,confirmed:true});assert.equal(preflight,1);assert.equal(f.applied.length,1);assert.equal(packageRequests(),2);
  assert.equal(await fs.readFile(downloaded.path,'utf8'),bytes.toString());
});
test('historical packages without manifests or compatible format information remain manual installations',async t=>{
  for(const [platform,arch,format,metadata,hasManifest] of [['darwin','arm64','zip',{},false],['win32','x64','exe',{},true],
    ['darwin','x64','dmg',{},true],['linux','x64','deb',{assets:{}},true],['darwin','arm64','zip',{dataFormat:'future-data-v2'},true],
    ['darwin','arm64','zip',{dataFormat:undefined},true]]){
    const row=historyRelease('2.2.0',{platform,arch,hasManifest}),f=await historyFixture(t,{platform,arch,rows:[row],metadata,install:async()=>({state:'downloaded'})});
    const listed=await f.updater.listVersions(),checked=await f.updater.selectVersion(listed.releases[0].assets.find(item=>item.format===format).id);
    assert.equal(checked.state,'available');assert.equal(checked.manualInstall,true);assert.equal(checked.format,format);
    assert.equal(f.updater.candidate.transition.dataFormat,'bridge-data-v1');assert.equal(f.updater.candidate.manualInstall,true);
    assert.equal((await f.updater.download({candidateId:checked.candidateId})).state,'downloaded');assert.equal(f.applied.length,0);
    const result=await f.updater.install({candidateId:checked.candidateId,confirmed:true});assert.equal(result.state,'downloaded');assert.equal(result.manualInstall,true);
    assert.equal(f.applied[0].manualInstall,true);assert.equal(f.updater.cacheStatus().entries[0].format,format);
  }
  const f=await historyFixture(t,{metadata:{dataFormat:undefined},rows:[historyRelease('2.2.1')]});
  const listed=await f.updater.listVersions(),checked=await f.updater.selectVersion(listed.releases[0].assets[0].id);
  assert.equal(checked.manualInstall,false);assert.equal(f.updater.candidate.transition.dataFormat,'bridge-data-v1');
});
test('bad signed manifests and mismatched digests cannot downgrade a historical selection to manual',async t=>{
  for(const defect of ['signature','digest','size','version']){
    const f=await historyFixture(t,{metadata:defect==='digest'?{assets:{'darwin-arm64':{name:assetName('2.2.2',platform,arch),size:bytes.length,sha256:'b'.repeat(64)}}}:
      defect==='size'?{assets:{'darwin-arm64':{name:assetName('2.2.2',platform,arch),size:bytes.length+1,sha256:createHash('sha256').update(bytes).digest('hex')}}}:defect==='version'?{version:'2.2.1'}:{}});
    const listed=await f.updater.listVersions();if(defect==='signature')f.updater.key=generateKeyPairSync('ed25519').publicKey;
    const result=await f.updater.selectVersion(listed.releases[0].assets[0].id);assert.equal(result.state,'error');assert.equal(f.updater.candidate,null);
    await assert.rejects(f.updater.download({candidateId:'old'}),/检查/);assert.equal(f.applied.length,0);
  }
});
test('manual historical downloads still reject incorrect package bytes',async t=>{
  const f=await historyFixture(t,{rows:[historyRelease('1.0.0',{hasManifest:false})],packageBytes:Buffer.from('bad bytes')});
  const listed=await f.updater.listVersions(),checked=await f.updater.selectVersion(listed.releases[0].assets[0].id);
  assert.equal((await f.updater.download({candidateId:checked.candidateId})).state,'error');assert.deepEqual(await fs.readdir(f.directory),[]);assert.equal(f.applied.length,0);
});
test('manual installer handoff protects only its package until the updater restarts',async t=>{
  for(const platform of ['darwin','win32','linux']){
    const f=await historyFixture(t,{platform,arch:platform==='darwin'?'arm64':'x64',rows:[historyRelease('1.0.0',{platform,arch:platform==='darwin'?'arm64':'x64',hasManifest:false})],install:async()=>({state:'installer-handoff'})});
    const listed=await f.updater.listVersions(),checked=await f.updater.selectVersion(listed.releases[0].assets[0].id);
    const result=await f.updater.install({candidateId:checked.candidateId,confirmed:true});assert.equal(result.state,'installer-handoff');assert.equal(f.updater.busy,false);
    const cache=f.updater.cacheStatus().entries[0];assert.equal(cache.inUse,true);assert.throws(()=>f.updater.cacheSelection([cache.id]),/更新完成/);
    f.updater.set('idle');assert.equal(f.updater.cacheStatus().entries[0].inUse,true);
    const other=listed.releases[0].assets[1];await f.updater.selectVersion(other.id);
    await f.updater.download({candidateId:f.updater.status().candidateId});assert.equal(f.updater.cacheStatus().entries.find(entry=>entry.format===other.format).inUse,false);
    await f.updater.selectVersion(listed.releases[0].assets[0].id);await fs.writeFile(path.join(f.directory,cache.id),'modified while installer open');
    assert.equal((await f.updater.download({candidateId:f.updater.status().candidateId})).state,'error');
    const restarted=new Updater({current:'2.2.2',directory:f.directory,install:async()=>{}});assert.equal(restarted.cacheStatus().entries[0].inUse,false);
  }
});
test('history selection is stable against background checks and only the latest explicit selection or check wins',async t=>{
  let release,reads=0;const wait=new Promise(resolve=>{release=resolve;});
  const f=await historyFixture(t,{manifestWait:async()=>{if(!reads++)await wait;}}),listed=await f.updater.listVersions();
  const first=f.updater.selectVersion(listed.releases[0].assets[0].id);await new Promise(setImmediate);
  const requests=f.requests.length;assert.equal((await f.updater.check()).selected,true);assert.equal(f.requests.length,requests);
  const second=await f.updater.selectVersion(listed.releases[1].assets[0].id);release();await first;
  assert.equal(f.updater.status().version,'2.2.2-preview.3');assert.equal(f.updater.status().candidateId,second.candidateId);
  assert.equal((await f.updater.check()).candidateId,second.candidateId);
  await assert.rejects(f.updater.download({candidateId:'stale'}),/检查/);
  await f.updater.check({manual:true});assert.equal(f.updater.status().selected,undefined);assert.equal(f.updater.candidate,null);
  const oldId=listed.releases[0].assets[0].id;await f.updater.listVersions();await assert.rejects(f.updater.selectVersion(oldId),/重新选择/);
});
test('a manual check cancels an in-flight historical selection without a stale candidate resurfacing',async t=>{
  let release,reads=0;const wait=new Promise(resolve=>{release=resolve;});
  const f=await historyFixture(t,{manifestWait:async()=>{if(!reads++)await wait;}}),list=await f.updater.listVersions();
  const pending=f.updater.selectVersion(list.releases[1].assets[0].id);await new Promise(setImmediate);
  assert.equal((await f.updater.check({manual:true})).state,'current');release();await pending;
  assert.equal(f.updater.candidate,null);assert.equal(f.updater.status().selected,undefined);assert.equal(f.updater.checking,false);
});
test('concurrent history refreshes publish only the most recent complete catalogue',async t=>{
  const f=await historyFixture(t);let release,reads=0;
  f.updater.catalogueFetch=async()=>{
    if(!reads++)return new Promise(resolve=>{release=()=>resolve(new Response(JSON.stringify([historyRelease('2.2.1')])));});
    return new Response(JSON.stringify([historyRelease('2.2.2-preview.3')]));
  };
  const old=f.updater.listVersions(),latest=await f.updater.listVersions();release();await assert.rejects(old,/重新选择/);
  assert.equal(latest.releases[0].version,'2.2.2-preview.3');
  assert.equal((await f.updater.selectVersion(latest.releases[0].assets[0].id)).version,'2.2.2-preview.3');
});
test('download, selection and checks cannot replace the active immutable package operation',async t=>{
  const f=await historyFixture(t),list=await f.updater.listVersions(),checked=await f.updater.selectVersion(list.releases[0].assets[0].id);
  let release,requested,packageRequests=0;const begun=new Promise(resolve=>{requested=resolve;}),original=f.updater.fetch;
  f.updater.fetch=async(url,options)=>url.endsWith('.zip')?new Promise(resolve=>{packageRequests++;release=()=>resolve(new Response(bytes));requested();}):original(url,options);
  const pending=f.updater.download({candidateId:checked.candidateId});await begun;
  assert.equal((await f.updater.selectVersion(list.releases[1].assets[0].id)).candidateId,checked.candidateId);
  assert.equal((await f.updater.check({manual:true})).candidateId,checked.candidateId);
  assert.equal((await f.updater.install({candidateId:checked.candidateId,confirmed:true})).state,'downloading');
  assert.equal((await f.updater.download({candidateId:checked.candidateId})).state,'downloading');
  release();assert.equal((await pending).state,'downloaded');assert.equal(f.applied.length,0);
  assert.equal(packageRequests,1);
});
