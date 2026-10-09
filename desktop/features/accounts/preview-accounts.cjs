'use strict';
const fs=require('node:fs'),path=require('node:path');

// Used only by the local-preview entry point. Never share a writable account store.
function seedPreviewAccounts(sourceDataDir,previewDataDir){
  const source=path.join(path.resolve(sourceDataDir),'accounts'),target=path.join(path.resolve(previewDataDir),'accounts');
  const marker=path.join(previewDataDir,'preview-accounts-import.json');
  if(source===target||fs.existsSync(marker)||!fs.existsSync(path.join(source,'index.json')))return 0;
  for(const directory of [source,target])if(fs.existsSync(directory)&&fs.lstatSync(directory).isSymbolicLink())throw Error('Account directory cannot be a symbolic link');
  const stateFile=path.join(source,'switch.json');
  if(fs.existsSync(stateFile)&&['preparing','stopping','applying','starting','verifying','restoring','interrupted'].includes(JSON.parse(fs.readFileSync(stateFile,'utf8')).phase))return 0;
  const original=fs.readFileSync(path.join(source,'index.json')),sourceIndex=JSON.parse(original);
  const indexFile=path.join(target,'index.json');
  const index=fs.existsSync(indexFile)?JSON.parse(fs.readFileSync(indexFile,'utf8')):{...sourceIndex,accounts:[]};
  const pending=sourceIndex.accounts.filter(row=>!index.accounts.some(saved=>saved.id===row.id)).map(row=>{
    if(!/^[a-f0-9]{32}$/.test(row.id)||!['chatgpt','api'].includes(row.kind))throw Error('Invalid saved account');
    const name=row.kind==='chatgpt'?'auth.json':'api.json';
    if(fs.existsSync(path.join(target,row.id)))throw Error('An unindexed preview account directory already exists');
    return {row,name,credentials:fs.readFileSync(path.join(source,row.id,name))};
  });
  if(!fs.readFileSync(path.join(source,'index.json')).equals(original))throw Error('Saved accounts changed during preview preparation; retry');
  fs.mkdirSync(previewDataDir,{recursive:true,mode:0o700});
  const staging=fs.mkdtempSync(path.join(previewDataDir,'.preview-accounts-'));
  try{
    for(const {row,name,credentials} of pending){
      const folder=path.join(staging,row.id);fs.mkdirSync(folder,{mode:0o700});
      fs.writeFileSync(path.join(folder,name),credentials,{mode:0o600,flag:'wx'});index.accounts.push(row);
    }
    fs.writeFileSync(path.join(staging,'index.json'),JSON.stringify(index),{mode:0o600,flag:'wx'});
    fs.mkdirSync(target,{recursive:true,mode:0o700});
    for(const {row} of pending)fs.renameSync(path.join(staging,row.id),path.join(target,row.id));
    fs.renameSync(path.join(staging,'index.json'),indexFile);
    fs.writeFileSync(marker,JSON.stringify({source:sourceDataDir,imported:pending.length}),{mode:0o600});
    return pending.length;
  }finally{fs.rmSync(staging,{recursive:true,force:true});}
}
module.exports={seedPreviewAccounts};
