'use strict';
// Release metadata is authenticated independently of macOS/Windows code signing.
const sync=require('node:fs'),fs=require('node:fs/promises'),path=require('node:path');
const {createHash,verify,randomUUID}=require('node:crypto');
const REPO='try2love/codex-mobile-bridge';
const RELEASES=`https://api.github.com/repos/${REPO}/releases?per_page=100`;
// The mirror only changes transport. Release selection still requires GitHub's
// manifest signature, and the ZIP is checked against its signed SHA-256.
const MIRROR='https://gh-proxy.com/';
const MIRROR_RETRIES=new Set([403,429,500,502,503,504]);
const MAX_PACKAGE=1024*1024*1024;
// The helper hands these values to its new GUI. Launch Services may also pass
// the same environment to a different installation well after the swap.
function activeUpdateHandoff(env,target,version,pid){
  const transaction=env.CMB_UPDATE_TRANSACTION,dataDir=env.CMB_UPDATE_DATA_DIR,token=env.CMB_UPDATE_TOKEN;
  if(!transaction||!token||!dataDir||!path.isAbsolute(transaction)||!path.isAbsolute(dataDir))return null;
  const read=name=>JSON.parse(sync.readFileSync(path.join(transaction,name),'utf8'));
  const same=(left,right)=>typeof left==='string'&&path.isAbsolute(left)&&path.relative(left,right)==='';
  try{
    if(!same(path.dirname(transaction),path.dirname(target))||!path.basename(transaction).startsWith('.cmb-update-')||
       !sync.lstatSync(transaction).isDirectory()||sync.existsSync(path.join(transaction,'result.json')))return null;
    const plan=read('plan.json');
    if(!same(plan.target,target)||!same(plan.dataDir,dataDir)||plan.token!==token||plan.version!==version||
       read('ready.json').token!==token)return null;
    // The helper records the PID immediately after spawning this GUI. A fast
    // child may reach startup first; recheck before writing its acknowledgement.
    const launcher=env.CMB_UPDATE_LAUNCHER_PID;
    if(launcher&&(!/^[1-9]\d*$/.test(launcher)||!Number.isSafeInteger(Number(launcher))||Number(launcher)===pid))return null;
    if(sync.existsSync(path.join(transaction,'launched.json'))&&read('launched.json').pid!==(launcher?Number(launcher):pid))return null;
    if(launcher&&sync.existsSync(path.join(transaction,'launch-services.json'))){
      const receipt=read('launch-services.json');
      if(receipt.pid!==pid||receipt.launcherPid!==Number(launcher)||receipt.token!==token)return null;
    }
    return {transaction,token,dataDir:path.resolve(dataDir)};
  }catch{return null;}
}
function version(value){
  const match=/^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-(alpha|beta|preview|rc)\.(0|[1-9]\d*))?$/.exec(value);
  if(!match)throw Error('更新版本格式无效。');
  const parts=[...match.slice(1,4).map(Number),{alpha:0,beta:1,preview:2,rc:3}[match[4]]??4,Number(match[5]||0)];
  if(parts.some(v=>!Number.isSafeInteger(v)))throw Error('更新版本格式无效。');
  return parts;
}
function compare(a,b){const x=version(a),y=version(b);for(let i=0;i<x.length;i++)if(x[i]!==y[i])return x[i]>y[i]?1:-1;return 0;}
function releaseUrl(v,name){version(v);return `https://github.com/${REPO}/releases/download/v${v}/${name}`;}
function allowedUrl(value){
  const u=new URL(value);
  if(u.protocol!=='https:'||u.username||u.password||u.port||u.hash)throw Error('更新地址未通过来源检查。');
  if(value===RELEASES||u.hostname==='github.com'&&u.pathname.startsWith(`/${REPO}/releases/download/`)||['release-assets.githubusercontent.com','objects.githubusercontent.com'].includes(u.hostname))return u.href;
  throw Error('更新地址未通过来源检查。');
}
function mirrorUrl(value){
  allowedUrl(value);
  return MIRROR+value;
}
function allowedMirrorUrl(value){
  if(value.startsWith(MIRROR)){
    const nested=value.slice(MIRROR.length);
    allowedUrl(nested);
    return value;
  }
  return allowedUrl(value);
}
function isAbort(error){return error?.name==='AbortError'||error?.code==='ABORT_ERR';}
async function fetchWithMirror(fetch,url,{signal,headers}){
  const officialHeaders={...headers};
  const attempts=[['official',url,officialHeaders]];
  if(signal?.aborted)return fetch(allowedUrl(url),{signal,headers:officialHeaders});
  try{allowedUrl(url);}catch(error){if(isAbort(error))throw error;throw error;}
  attempts.push(['mirror',mirrorUrl(url),officialHeaders]);
  let lastError;
  for(const [index,[label,target,requestHeaders]] of attempts.entries()){
    try{
      const response=await fetch(allowedMirrorUrl(target),{signal,headers:requestHeaders});
      if(response.ok||label==='mirror'||!MIRROR_RETRIES.has(response.status))return response;
      await response.body?.cancel();
      lastError=Error(`更新服务返回 ${response.status}。`);
    }catch(error){
      if(isAbort(error))throw error;
      lastError=error;
      if(index===attempts.length-1)throw error;
    }
  }
  throw lastError||Error('无法获取更新，请检查网络后重试。');
}
function assetName(v,platform,arch,format='deb'){
  version(v);
  if(!({darwin:['arm64','x64'],win32:['x64'],linux:['arm64','x64']}[platform]||[]).includes(arch))throw Error('此系统暂无应用内更新包。');
  if(platform==='linux'){
    if(!['deb','AppImage'].includes(format))throw Error('此系统暂无应用内更新包。');
    return `Codex-Mobile-Bridge-${v}-Linux-${arch==='x64'?(format==='deb'?'amd64':'x86_64'):arch}.${format}`;
  }
  return `Codex-Mobile-Bridge-${v}-${platform==='darwin'?'macOS':'Windows'}-${arch}.zip`;
}
function currentChannel(current){version(current);return current.includes('-')?'preview':'stable';}
function returnCompatible(current,target,dataFormat){
  // Only this reviewed data-format pair may cross the anti-downgrade boundary.
  return /^2\.2\.2-preview\.\d+$/.test(current)&&target==='2.2.1'&&(!dataFormat||dataFormat==='bridge-data-v1');
}
function manifest(bytes,key,{current,platform,arch,expected,format='deb',channel=currentChannel(current),intent='update'}){
  const envelope=JSON.parse(bytes.toString('utf8'));
  if(typeof envelope.payload!=='string'||typeof envelope.signature!=='string')throw Error('更新清单签名无效。');
  const payload=Buffer.from(envelope.payload,'base64'),signature=Buffer.from(envelope.signature,'base64');
  if(signature.length!==64||!verify(null,payload,key,signature))throw Error('更新清单签名无效。');
  const value=JSON.parse(payload.toString('utf8'));
  if(value.schema!==1||value.version!==expected)throw Error('更新版本不适用于当前应用。');
  const direction=compare(value.version,current),returning=intent==='return-stable'&&currentChannel(current)==='preview'&&channel==='stable'&&!value.version.includes('-');
  if(direction===0||direction<0&&(!returning||!returnCompatible(current,value.version,value.dataFormat))||
    channel==='stable'&&value.version.includes('-')||!current.includes('-')&&value.version.includes('-')&&!(channel==='preview'&&intent==='opt-in-preview'))throw Error('更新版本不适用于当前应用。');
  if(value.dataFormat!==undefined&&value.dataFormat!=='bridge-data-v1')throw Error('此版本的数据格式兼容性尚未确认，已取消安装。');
  const asset=value.assets?.[`${platform}-${arch}${platform==='linux'?'-'+format:''}`],name=assetName(value.version,platform,arch,format);
  // Historical releases without signed Linux hashes can only offer the official
  // release page. Their public checksum file is not an authenticated manifest.
  if(platform==='linux'&&!asset)return {version:value.version,notes:typeof value.notes==='string'?value.notes.slice(0,20000):'',manualRelease:true,
    releasePage:`https://github.com/${REPO}/releases/tag/v${value.version}`,transition:{intent,from:current,to:value.version,dataFormat:value.dataFormat||'bridge-data-v1'}};
  if(!asset||asset.name!==name||!Number.isSafeInteger(asset.size)||asset.size<=0||asset.size>MAX_PACKAGE||!/^[a-f0-9]{64}$/.test(asset.sha256))throw Error('更新包校验信息不完整。');
  return {version:value.version,notes:typeof value.notes==='string'?value.notes.slice(0,20000):'',asset:{...asset,url:releaseUrl(value.version,name)},
    transition:{intent,from:current,to:value.version,dataFormat:value.dataFormat||'bridge-data-v1'}};
}
async function transfer(fetch,url,{limit,signal,file,onProgress=()=>{}}){
  const response=await fetchWithMirror(fetch,url,{signal,headers:{'User-Agent':'Codex-Mobile-Bridge','Accept':url===RELEASES?'application/vnd.github+json':'application/octet-stream'}});
  if(!response.ok){await response.body?.cancel();throw Error('无法获取更新，请检查网络后重试。');}
  const reader=response.body.getReader(),chunks=[],hash=createHash('sha256');let size=0,handle;
  try{
    if(file)handle=await fs.open(file,'wx',0o600);
    for(;;){const {done,value}=await reader.read();if(done)break;size+=value.length;if(size>limit)throw Error('更新下载超出大小限制。');
      const bytes=Buffer.from(value);hash.update(bytes);
      if(handle)await handle.writeFile(bytes);else chunks.push(bytes);
      onProgress(size);
    }
    return {size,sha256:hash.digest('hex'),bytes:file?undefined:Buffer.concat(chunks)};
  }finally{await handle?.close();await reader.cancel();}
}
function cacheName(candidate,platform,arch,format){
  if(platform==='linux')return `verified-${candidate.asset.sha256}-${candidate.asset.name}`;
  return `verified-${candidate.version}-${platform}-${arch}-${candidate.asset.sha256}.zip`;
}
async function digestFile(file){
  const hash=createHash('sha256');let size=0;
  const handle=await fs.open(file,'r');
  try{
    for(;;){const {bytesRead,buffer}=await handle.read({buffer:Buffer.allocUnsafe(1024*1024),length:1024*1024,position:null});
      if(!bytesRead)break;size+=bytesRead;hash.update(buffer.subarray(0,bytesRead));}
  }finally{await handle.close();}
  return {size,sha256:hash.digest('hex')};
}
async function validCache(file,asset){
  try{
    const info=await fs.lstat(file);
    if(!info.isFile()||info.isSymbolicLink())return false;
    const value=await digestFile(file);
    return value.size===asset.size&&value.sha256===asset.sha256;
  }catch{return false;}
}
async function cleanDownloadStaging(directory){
  try{
    for(const entry of await fs.readdir(directory,{withFileTypes:true}))
      if(entry.isDirectory()&&/^download-[A-Za-z0-9]+$/.test(entry.name))
        await fs.rm(path.join(directory,entry.name),{recursive:true,force:true});
  }catch(error){if(error.code!=='ENOENT')throw error;}
}
class Updater{
  constructor({current,platform=process.platform,arch=process.arch,format='deb',key,fetch,directory,install,preflight,supported=true,skipFile}){
    Object.assign(this,{current,platform,arch,format,key,fetch,directory,apply:install,preflight,supported,skipFile});
    this.currentChannel=currentChannel(current);this.channel=this.currentChannel;this.checkToken=0;
    this.set(supported?'idle':'unsupported');
  }
  get skipIdentity(){return this.platform+'-'+this.arch+(this.platform==='linux'?'-'+this.format:'')+':'+this.channel;}
  status(){return {...this.state};}
  get busy(){return ['downloading','preparing','restarting'].includes(this.state.state);}
  set(state,extra={}){this.state={current:this.current,currentChannel:this.currentChannel,channel:this.channel,...extra,state};return this.status();}
  details(candidate){return {version:candidate.version,notes:candidate.notes,candidateId:candidate.id,intent:candidate.transition.intent,
    requiresConfirmation:candidate.transition.intent!=='update',...(this.platform==='linux'?{manualInstall:true}:{}),...(candidate.manualRelease?{manualRelease:true}:{})};}
  async skippedVersions(){
    if(!this.skipFile)return this.skipped||{};
    try{const value=JSON.parse(await fs.readFile(this.skipFile,'utf8'));return value&&typeof value==='object'&&!Array.isArray(value)?value:{};}catch{return {};}
  }
  async skippedVersion(){
    const value=(await this.skippedVersions())[this.skipIdentity];return typeof value==='string'?value:'';
  }
  async skip(){
    if(this.checking||this.busy||!this.candidate||this.state.state!=='available')throw Error('请先检查更新。');
    if(this.skipping)return this.status();
    this.skipping=true;
    try{
      const candidate=this.candidate,identity=this.skipIdentity,version=candidate.version;
      const skipped={...await this.skippedVersions(),[identity]:version};
      if(this.skipFile){await fs.mkdir(path.dirname(this.skipFile),{recursive:true,mode:0o700});await fs.writeFile(this.skipFile,JSON.stringify(skipped),{mode:0o600});}
      else this.skipped=skipped;
      return this.candidate===candidate?this.set('skipped',this.details(candidate)):this.status();
    }finally{this.skipping=false;}
  }
  async cleanupVerifiedPackages({startup=false,preserveCurrent=false}={}){
    try{
      for(const entry of await fs.readdir(this.directory,{withFileTypes:true})){
        if(!entry.isFile())continue;
        const match=entry.name.match(/^verified-(\d+\.\d+\.\d+(?:-(?:alpha|beta|preview|rc)\.\d+)?)-(?:darwin|win32)-(?:x64|arm64)-[a-f0-9]{64}\.zip$/)||
          entry.name.match(/^verified-[a-f0-9]{64}-Codex-Mobile-Bridge-(\d+\.\d+\.\d+(?:-(?:alpha|beta|preview|rc)\.\d+)?)-Linux-(?:amd64|x86_64|arm64)\.(?:deb|AppImage)$/);
        if(match&&(!startup||!preserveCurrent&&match[1]===this.current))
          await fs.rm(path.join(this.directory,entry.name),{force:true});
      }
    }catch(error){if(error.code!=='ENOENT')throw error;}
  }
  async check({manual=false,channel}={}){
    if(channel!==undefined&&(!manual||!['stable','preview'].includes(channel)))throw Error('更新渠道无效。');
    const selected=channel||this.channel;
    if(this.busy||this.skipping||!this.supported||!manual&&this.channel!==this.currentChannel||this.checking&&selected===this.channel)return this.status();
    this.channel=selected;this.checkAbort?.abort();this.checkAbort=new AbortController();
    const token=++this.checkToken,active=()=>token===this.checkToken;
    this.checking=true;this.candidate=null;this.set('checking');
    const intent=selected===this.currentChannel?'update':selected==='preview'?'opt-in-preview':'return-stable';
    try{
      assetName(this.current,this.platform,this.arch,this.format);
      const signal=AbortSignal.any([this.checkAbort.signal,AbortSignal.timeout(30000)]);
      const releases=JSON.parse((await transfer(this.fetch,RELEASES,{signal,limit:4*1024*1024})).bytes);
      if(!active())return this.status();
      if(!Array.isArray(releases))throw Error('更新列表无效。');
      const candidates=releases.filter(item=>{
        if(item.draft||!/^v/.test(item.tag_name||''))return false;
        const target=item.tag_name.slice(1);
        try{return (selected!=='stable'||!item.prerelease&&!target.includes('-'))&&(intent!=='opt-in-preview'||target.includes('-'))&&
          (intent==='return-stable'?compare(target,this.current)!==0:compare(target,this.current)>0)&&item.assets?.some(a=>a.name==='bridge-update.json');}catch{return false;}
      }).sort((a,b)=>compare(b.tag_name.slice(1),a.tag_name.slice(1)));
      if(!candidates.length)return this.set('current');
      const expected=candidates[0].tag_name.slice(1);
      const bytes=(await transfer(this.fetch,releaseUrl(expected,'bridge-update.json'),{signal,limit:256*1024})).bytes;
      if(!active())return this.status();
      const value=manifest(bytes,this.key,{current:this.current,platform:this.platform,arch:this.arch,format:this.format,expected,channel:selected,intent});
      const candidate=Object.freeze({...value,id:randomUUID(),asset:Object.freeze(value.asset),transition:Object.freeze(value.transition)});
      const skipped=(await this.skippedVersion())===expected;
      if(!active())return this.status();
      this.candidate=candidate;
      return this.set(skipped&&!manual?'skipped':'available',{...this.details(candidate),...(skipped&&manual?{skipped:true}:{})});
    }catch(error){return active()?this.set('error',{message:error.message}):this.status();}
    finally{if(active())this.checking=false;}
  }
  async install({candidateId,confirmed=false}={}){
    if(this.busy)return this.status();
    if(!this.candidate||this.checking||this.skipping||candidateId!==undefined&&candidateId!==this.candidate.id)throw Error('请先检查更新。');
    if(this.candidate.transition.intent!=='update'&&(!confirmed||candidateId!==this.candidate.id))throw Error('请确认所选版本后再安装。');
    const candidate=this.candidate;let staging,archive,retained=false;
    const details=this.details(candidate);
    this.set('preparing',details);
    try{
      if(candidate.manualRelease){await this.apply(candidate);return this.set('manual-release',details);}
      await this.preflight?.();
      this.set('downloading',{...details,received:0,total:candidate.asset.size});
      await fs.mkdir(this.directory,{recursive:true,mode:0o700});
      archive=path.join(this.directory,cacheName(candidate,this.platform,this.arch,this.format));
      if(await validCache(archive,candidate.asset)){
        this.set('downloading',{...details,received:candidate.asset.size,total:candidate.asset.size,cached:true});
      }else{
        await fs.rm(archive,{force:true});
        staging=await fs.mkdtemp(path.join(this.directory,'download-'));
        const temporary=path.join(staging,'package.tmp');
        const downloaded=await transfer(this.fetch,candidate.asset.url,{signal:AbortSignal.timeout(15*60*1000),limit:candidate.asset.size,file:temporary,
          onProgress:received=>this.set('downloading',{...details,received,total:candidate.asset.size})});
        if(downloaded.size!==candidate.asset.size||downloaded.sha256!==candidate.asset.sha256)throw Error('更新包校验失败，已取消安装。');
        await fs.rename(temporary,archive);
        await fs.rm(staging,{recursive:true,force:true});
        staging=undefined;
      }
      this.set('preparing',details);
      if(this.platform==='linux')retained=true;
      // Keep the verified archive until the replacement process has started the
      // new application and written its acknowledgement. A failed swap or
      // rollback must be able to retry without downloading the same bytes.
      const outcome=await this.apply({...candidate,archive});
      if(this.platform==='linux')return this.set(outcome?.state==='installer-handoff'?'installer-handoff':'downloaded',{...details,path:archive});
      return this.set('restarting',details);
    }catch(error){return this.set('error',{...details,message:error.message,...(retained?{path:archive}:{})});}
    finally{if(staging&&!retained)await fs.rm(staging,{recursive:true,force:true});}
  }
}
module.exports={Updater,activeUpdateHandoff,version,compare,manifest,assetName,allowedUrl,allowedMirrorUrl,mirrorUrl,releaseUrl,RELEASES,MIRROR,transfer,cacheName,validCache,cleanDownloadStaging};
