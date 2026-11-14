const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const {webcrypto,randomUUID}=require('node:crypto');
const [root,profile,firstSave]=process.argv.slice(2),stats={starts:0,sends:0,trust:0,files:0,attempts:0};
const api={
  getAll:async()=>[],checkTrust:async cwd=>{stats.trust++;return {trusted:cwd===root};},
  readFileAtCwd:async(cwd,name)=>({contents:fs.readFileSync(path.join(cwd,name),'utf8')}),
  writeSessionFile:async()=>{throw Error('no session exists');},
  start:async()=>{stats.starts++;throw Error('unexpected model turn');},
  sendMessage:async()=>{stats.sends++;throw Error('unexpected model turn');},
};
const app={'claude.web':{LocalSessions:api,FileSystem:{async savePastedFile(base64,mime,name){
  if(++stats.attempts===1&&firstSave==='reject')throw Error('first staging failed');
  const file=path.join(profile,randomUUID()+'-'+name);
  fs.writeFileSync(file,Buffer.from(base64,'base64'));stats.files++;return {path:file};
}}}};
const source=fs.readFileSync(path.join(root,'console-connect.js'),'utf8');
const tick=setInterval(()=>fs.writeFileSync(path.join(root,'counts.json'),JSON.stringify(stats)),100);
vm.runInNewContext(source,{window:app,crypto:webcrypto,btoa,TextEncoder,Uint8Array,console,setTimeout,clearTimeout})
  .catch(error=>{console.error(error);process.exitCode=1;}).finally(()=>clearInterval(tick));
