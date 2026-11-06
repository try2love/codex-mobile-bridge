'use strict';
const fs=require('node:fs');
const path=require('node:path');
const {fileURLToPath,pathToFileURL}=require('node:url');

function sourceFiles(root){
  const entry=path.join(root,'desktop/index.html');
  const files=new Set([entry,path.join(root,'web/features/accounts/wsl-accounts.js')]);
  let html;try{html=fs.readFileSync(entry,'utf8');}catch{return files;}
  for(const match of html.matchAll(/<(?:script|link)\b[^>]*\b(?:src|href)=["']([^"']+)["'][^>]*>/gi)){
    try{
      const url=new URL(match[1],pathToFileURL(entry));
      if(url.protocol!=='file:')continue;
      const file=fileURLToPath(url),relative=path.relative(root,file);
      if(!relative||relative.startsWith('..')||path.isAbsolute(relative)||!/^\.(?:html|css|js)$/.test(path.extname(file)))continue;
      if(!['desktop'+path.sep,'web'+path.sep].some(prefix=>relative.startsWith(prefix)))continue;
      files.add(file);
    }catch{}
  }
  return files;
}

function displayState(value){
  if(!value||typeof value!=='object'||Array.isArray(value))throw Error('Invalid development view');
  if(Object.keys(value).some(key=>!['tab','scroll','details'].includes(key)))throw Error('Invalid development view');
  if(!['overview','accounts','network','devices','notifications','advanced'].includes(value.tab))throw Error('Invalid development tab');
  if(!Number.isFinite(value.scroll)||value.scroll<0||value.scroll>1e7)throw Error('Invalid development scroll');
  if(!Array.isArray(value.details)||value.details.length>128||value.details.some(id=>typeof id!=='string'||!/^[\w-]{1,100}$/.test(id)))throw Error('Invalid development details');
  return {tab:value.tab,scroll:value.scroll,details:[...value.details]};
}

function createDevelopmentReload({root,enabled,notify,reload,canReload=()=>true,setTimer=setTimeout,clearTimer=clearTimeout,setPoll=setInterval,clearPoll=clearInterval}){
  let revision=0,view=null,reloaded=false,pending=null,closed=false,timer,pollTimer,reloadTimer;
  const changes=new Set();
  function fingerprints(){
    const result=new Map();
    for(const file of sourceFiles(root)){
      try{
        const stat=fs.lstatSync(file);
        if(stat.isFile())result.set(file,`${stat.ino}:${stat.size}:${stat.mtimeMs}:${stat.ctimeMs}`);
      }catch{}
    }
    return result;
  }
  let previous=enabled?fingerprints():new Map();
  function publish(){
    timer=null;if(closed||!changes.size)return;
    const files=[...new Set([...(pending?.files||[]),...[...changes].map(file=>path.relative(root,file).split(path.sep).join('/'))])].sort();
    changes.clear();
    const change={revision:++revision,files,stylesOnly:files.every(file=>file.endsWith('.css'))};
    if(!change.stylesOnly)pending=change;
    notify(change);
  }
  function poll(){
    if(!enabled||closed)return;
    const current=fingerprints();
    for(const file of new Set([...previous.keys(),...current.keys()]))if(previous.get(file)!==current.get(file))changes.add(file);
    previous=current;
    if(changes.size){clearTimer(timer);timer=setTimer(publish,200);timer?.unref?.();}
  }
  if(enabled){pollTimer=setPoll(poll,500);pollTimer?.unref?.();}
  return {
    poll,
    state:()=>({reloaded,view,pending}),
    request(value){
      if(!enabled||closed||!pending||value?.revision!==pending.revision||!canReload())return {accepted:false};
      const next=displayState(value.view);
      if(reloadTimer)return {accepted:false};
      // Reply before replacing the renderer; never restart its workers or gateway.
      reloadTimer=setTimer(()=>{
        reloadTimer=null;
        if(closed)return;
        if(!canReload()){notify(pending);return;}
        view=next;reloaded=true;pending=null;reload();
      },0);
      return {accepted:true};
    },
    close(){closed=true;clearPoll(pollTimer);clearTimer(timer);clearTimer(reloadTimer);changes.clear();pending=null;view=null;}
  };
}

module.exports={sourceFiles,displayState,createDevelopmentReload};
