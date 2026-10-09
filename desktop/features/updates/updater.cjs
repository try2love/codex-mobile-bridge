'use strict';
// Release metadata is authenticated independently of macOS/Windows code signing.
const fs=require('node:fs/promises'),path=require('node:path');
const {createHash,verify}=require('node:crypto');
const REPO='try2love/codex-mobile-bridge';
const RELEASES=`https://api.github.com/repos/${REPO}/releases?per_page=100`;
// The mirror only changes transport. Release selection still requires GitHub's
// manifest signature, and the ZIP is checked against its signed SHA-256.
const MIRROR='https://gh-proxy.com/';
const MIRROR_RETRIES=new Set([403,429,500,502,503,504]);
const MAX_PACKAGE=1024*1024*1024;
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
function assetName(v,platform,arch){
  version(v);
  if(!({darwin:['arm64','x64'],win32:['x64']}[platform]||[]).includes(arch))throw Error('此系统暂无应用内更新包。');
  return `Codex-Mobile-Bridge-${v}-${platform==='darwin'?'macOS':'Windows'}-${arch}.zip`;
}
function manifest(bytes,key,{current,platform,arch,expected}){
  const envelope=JSON.parse(bytes.toString('utf8'));
  if(typeof envelope.payload!=='string'||typeof envelope.signature!=='string')throw Error('更新清单签名无效。');
  const payload=Buffer.from(envelope.payload,'base64'),signature=Buffer.from(envelope.signature,'base64');
  if(signature.length!==64||!verify(null,payload,key,signature))throw Error('更新清单签名无效。');
  const value=JSON.parse(payload.toString('utf8'));
  if(value.schema!==1||value.version!==expected||compare(value.version,current)<=0||!current.includes('-')&&value.version.includes('-'))throw Error('更新版本不适用于当前应用。');
  const asset=value.assets?.[`${platform}-${arch}`],name=assetName(value.version,platform,arch);
  if(!asset||asset.name!==name||!Number.isSafeInteger(asset.size)||asset.size<=0||asset.size>MAX_PACKAGE||!/^[a-f0-9]{64}$/.test(asset.sha256))throw Error('更新包校验信息不完整。');
  return {version:value.version,notes:typeof value.notes==='string'?value.notes.slice(0,20000):'',asset:{...asset,url:releaseUrl(value.version,name)}};
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
class Updater{
  constructor({current,platform=process.platform,arch=process.arch,key,fetch,directory,install,supported=true}){
    Object.assign(this,{current,platform,arch,key,fetch,directory,apply:install,supported});
    this.state={state:supported?'idle':'unsupported',current};
  }
  status(){return {...this.state};}
  get busy(){return ['downloading','preparing','restarting'].includes(this.state.state);}
  set(state,extra={}){this.state={current:this.current,...extra,state};return this.status();}
  async check(){
    if(this.busy||this.checking||!this.supported)return this.status();
    this.checking=true;this.candidate=null;this.set('checking');
    try{
      assetName(this.current,this.platform,this.arch);
      const signal=AbortSignal.timeout(30000);
      const releases=JSON.parse((await transfer(this.fetch,RELEASES,{signal,limit:4*1024*1024})).bytes);
      if(!Array.isArray(releases))throw Error('更新列表无效。');
      const candidates=releases.filter(item=>{
        if(item.draft||!/^v/.test(item.tag_name||'')||!this.current.includes('-')&&item.prerelease)return false;
        try{return compare(item.tag_name.slice(1),this.current)>0&&(this.current.includes('-')||!item.tag_name.includes('-'))&&item.assets?.some(a=>a.name==='bridge-update.json');}catch{return false;}
      }).sort((a,b)=>compare(b.tag_name.slice(1),a.tag_name.slice(1)));
      if(!candidates.length)return this.set('current');
      const expected=candidates[0].tag_name.slice(1);
      const bytes=(await transfer(this.fetch,releaseUrl(expected,'bridge-update.json'),{signal,limit:256*1024})).bytes;
      this.candidate=manifest(bytes,this.key,{current:this.current,platform:this.platform,arch:this.arch,expected});
      return this.set('available',{version:expected,notes:this.candidate.notes});
    }catch(error){return this.set('error',{message:error.message});}
    finally{this.checking=false;}
  }
  async install(){
    if(this.busy)return this.status();
    if(!this.candidate||this.checking)throw Error('请先检查更新。');
    const candidate=this.candidate;let staging;
    this.set('downloading',{version:candidate.version,received:0,total:candidate.asset.size});
    try{
      await fs.mkdir(this.directory,{recursive:true,mode:0o700});
      staging=await fs.mkdtemp(path.join(this.directory,'download-'));
      const archive=path.join(staging,'update.zip');
      const downloaded=await transfer(this.fetch,candidate.asset.url,{signal:AbortSignal.timeout(15*60*1000),limit:candidate.asset.size,file:archive,
        onProgress:received=>this.set('downloading',{version:candidate.version,received,total:candidate.asset.size})});
      if(downloaded.size!==candidate.asset.size||downloaded.sha256!==candidate.asset.sha256)throw Error('更新包校验失败，已取消安装。');
      this.set('preparing',{version:candidate.version});
      await this.apply({...candidate,archive});
      return this.set('restarting',{version:candidate.version});
    }catch(error){return this.set('error',{message:error.message,version:candidate.version});}
    finally{if(staging)await fs.rm(staging,{recursive:true,force:true});}
  }
}
module.exports={Updater,version,compare,manifest,assetName,allowedUrl,allowedMirrorUrl,mirrorUrl,releaseUrl,RELEASES,MIRROR,transfer};
