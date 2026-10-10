'use strict';
// Desktop-only installer: official HTTPS assets, explicit SHA-256 verification,
// no administrator privileges, shell commands, services or global PATH changes.
const fs=require('node:fs/promises'),path=require('node:path');
const {createHash}=require('node:crypto'),{gunzipSync}=require('node:zlib');
const {execFile}=require('node:child_process'),{promisify}=require('node:util');
const execute=promisify(execFile);
const {Readable}=require('node:stream');
const RELEASE='https://api.github.com/repos/cloudflare/cloudflared/releases/latest';
const LIMIT=128*1024*1024;
function assetName(platform=process.platform,arch=process.arch){
  const suffix={darwin:{arm64:'darwin-arm64.tgz',x64:'darwin-amd64.tgz'},win32:{x64:'windows-amd64.exe',ia32:'windows-386.exe'},linux:{x64:'linux-amd64',arm64:'linux-arm64'}}[platform]?.[arch];
  if(!suffix)throw Error('此系统暂不支持一键安装，请按官方教程手动安装。');
  return 'cloudflared-'+suffix;
}
function allowedUrl(value){
  const url=new URL(value);
  if(url.protocol!=='https:'||url.username||url.password||url.port)throw Error('下载地址未通过官方来源检查。');
  const valid=url.hostname==='api.github.com'&&url.pathname==='/repos/cloudflare/cloudflared/releases/latest'
    ||url.hostname==='github.com'&&url.pathname.startsWith('/cloudflare/cloudflared/releases/download/')
    ||['release-assets.githubusercontent.com','objects.githubusercontent.com'].includes(url.hostname);
  if(!valid)throw Error('下载地址未通过官方来源检查。');
  return url.href;
}
function electronFetch(net,validate=allowedUrl){
  // net.fetch cancels manual redirects instead of returning their Location.
  // net.request lets us inspect each hop before following it, with system proxy support.
  return (url,{signal,headers})=>new Promise((resolve,reject)=>{
    const request=net.request({url:validate(url),redirect:'manual',credentials:'omit',useSessionCookies:false});
    let redirects=0;
    const abort=()=>{reject(signal.reason);request.abort();};
    request.on('error',reject);
    request.on('close',()=>signal.removeEventListener('abort',abort));
    request.on('redirect',(_status,_method,next)=>{
      try{validate(next);if(++redirects>5)throw Error('官方下载跳转次数过多。');request.followRedirect();}
      catch(error){reject(error);request.abort();}
    });
    request.on('response',response=>{
      resolve({ok:response.statusCode>=200&&response.statusCode<300,status:response.statusCode,body:Readable.toWeb(response)});
    });
    for(const [name,value] of Object.entries(headers))request.setHeader(name,value);
    signal.addEventListener('abort',abort,{once:true});
    if(signal.aborted)abort();else request.end();
  });
}
async function download(fetch,url,limit,signal,progress=()=>{}){
  for(let redirects=0;redirects<6;redirects++){
    const response=await fetch(allowedUrl(url),{redirect:'manual',credentials:'omit',signal,headers:{'User-Agent':'Codex-Mobile-Bridge','Accept':url===RELEASE?'application/vnd.github+json':'application/octet-stream'}});
    if([301,302,303,307,308].includes(response.status)){
      const location=response.headers.get('location');await response.body?.cancel();
      if(!location)throw Error('官方下载返回了无效跳转。');
      url=new URL(location,url).href;continue;
    }
    if(!response.ok){await response.body?.cancel();throw Error('官方下载失败，请检查网络或按教程手动安装。');}
    const reader=response.body.getReader(),chunks=[];let size=0;
    try{
      for(;;){const {done,value}=await reader.read();if(done)break;size+=value.length;
        if(size>limit)throw Error('下载文件大小超出限制。');chunks.push(Buffer.from(value));progress(size);}
    }finally{await reader.cancel();}
    return Buffer.concat(chunks);
  }
  throw Error('官方下载跳转次数过多。');
}
function executableFromArchive(bytes,name){
  if(name.endsWith('.exe')||['cloudflared-linux-amd64','cloudflared-linux-arm64'].includes(name))return bytes;
  // Read the single regular executable into memory. Never extract archive paths.
  const tar=gunzipSync(bytes,{maxOutputLength:LIMIT});let found;
  for(let offset=0;offset+512<=tar.length;){
    const header=tar.subarray(offset,offset+512);if(header.every(byte=>byte===0))break;
    const string=(a,b)=>header.subarray(a,b).toString('utf8').replace(/\0.*$/s,'');
    const sizeText=string(124,136).trim();
    if(!/^[0-7]+$/.test(sizeText))throw Error('官方下载包格式不正确。');
    const size=parseInt(sizeText,8),prefix=string(345,500),entry=(prefix?prefix+'/':'')+string(0,100);
    if(!Number.isSafeInteger(size)||offset+512+size>tar.length)throw Error('官方下载包不完整。');
    if(['cloudflared','./cloudflared'].includes(entry)){
      if(found||![0,48].includes(header[156])||size===0)throw Error('官方下载包中的程序格式不正确。');
      found=tar.subarray(offset+512,offset+512+size);
    }
    offset+=512+Math.ceil(size/512)*512;
  }
  if(!found)throw Error('官方下载包中未找到 cloudflared。');
  return found;
}
const digest=bytes=>createHash('sha256').update(bytes).digest('hex');
async function probe(executable){
  try{
    const {stdout}=await execute(executable,['--version'],{timeout:10000,maxBuffer:65536,windowsHide:true});
    const version=stdout.trim();if(!/^cloudflared version \S+/.test(version))throw Error();return version;
  }catch{throw Error('cloudflared 无法运行，请确认系统版本、程序架构和执行权限，或重新安装。');}
}
async function install({dataDir,fetch,onProgress=()=>{},platform=process.platform,arch=process.arch,check=probe}){
  const name=assetName(platform,arch),signal=AbortSignal.timeout(180000);let staging;
  try{
    onProgress({state:'metadata',message:'正在获取 Cloudflare 官方版本…'});
    const release=JSON.parse((await download(fetch,RELEASE,2*1024*1024,signal)).toString('utf8'));
    const asset=release.assets?.find(item=>item.name===name),version=release.tag_name;
    if(typeof version!=='string'||!/^[\w.-]{1,80}$/.test(version)||!asset||!/^sha256:[a-f0-9]{64}$/.test(asset.digest||''))throw Error('官方版本缺少 SHA-256 校验信息，请按教程手动安装。');
    if(!Number.isSafeInteger(asset.size)||asset.size<=0||asset.size>LIMIT)throw Error('下载文件大小超出限制。');
    const expected='https://github.com/cloudflare/cloudflared/releases/download/'+version+'/'+name;
    if(asset.browser_download_url!==expected)throw Error('下载地址未通过官方来源检查。');
    const bytes=await download(fetch,expected,asset.size,signal,received=>onProgress({state:'download',message:'正在下载官方程序…',received,total:asset.size}));
    if(bytes.length!==asset.size||'sha256:'+digest(bytes)!==asset.digest)throw Error('SHA-256 校验失败，已取消安装，请重试或手动下载。');
    onProgress({state:'verify',message:'正在校验并安装 cloudflared…'});
    const executable=executableFromArchive(bytes,name),bin=path.join(dataDir,'bin');
    await fs.mkdir(bin,{recursive:true,mode:0o700});
    staging=await fs.mkdtemp(path.join(bin,'.cloudflared-'));
    const filename=platform==='win32'?'cloudflared.exe':'cloudflared',temporary=path.join(staging,filename);
    await fs.writeFile(temporary,executable,{mode:0o700,flag:'wx'});
    const verifiedVersion=await check(temporary);
    const destination=path.join(bin,'cloudflared-'+version+'-'+arch),target=path.join(destination,filename);
    try{await fs.access(destination);
      if(digest(await fs.readFile(target))!==digest(executable))throw Error('安装目录已有不同程序，请手动选择程序或移走冲突目录。');
    }catch(error){if(error.code!=='ENOENT')throw error;await fs.rename(staging,destination);staging=null;}
    const result={path:target,version:verifiedVersion};onProgress({state:'ready',message:'已安装并验证 cloudflared，请保存配置后启动网关。',...result});return result;
  }catch(error){
    if(error.name==='TimeoutError'||error.name==='AbortError')throw Error('下载超时，请检查网络后重试，或按官方教程手动安装。');
    if(error instanceof TypeError)throw Error('无法下载官方程序，请检查网络后重试，或按官方教程手动安装。');
    throw error;
  }finally{if(staging)await fs.rm(staging,{recursive:true,force:true});}
}
module.exports={assetName,allowedUrl,electronFetch,download,executableFromArchive,digest,probe,install};
