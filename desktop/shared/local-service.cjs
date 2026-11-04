'use strict';
// Local desktop-to-gateway RPC. No browser-facing port or credential forwarding.
const fs=require('node:fs'),path=require('node:path'),os=require('node:os'),net=require('node:net');
const {randomBytes,timingSafeEqual}=require('node:crypto');
const MAX=64*1024*1024;
async function serveLocal(profile,name,handler,{worker}={}){
  if(!/^[a-z-]+$/.test(name))throw Error('Invalid local service name');
  if(process.platform==='win32')return require('./local-service-windows.cjs').serveWindowsLocal(profile,name,handler,worker);
  const directory=fs.mkdtempSync(path.join(os.tmpdir(),'cmb-rpc-'));fs.chmodSync(directory,0o700);
  const endpoint=path.join(directory,'s'),record=path.join(profile,name+'-ipc.json'),token=randomBytes(32).toString('hex');
  const peers=new Set();
  const server=net.createServer(socket=>{
    if(peers.size>=16){socket.destroy();return;}peers.add(socket);socket.on('close',()=>peers.delete(socket));
    socket.on('error',()=>{});socket.setTimeout(185000,()=>socket.destroy());
    let buffer=Buffer.alloc(0),size=null,started=false;
    socket.on('data',chunk=>{
      if(started){socket.destroy();return;}
      buffer=Buffer.concat([buffer,chunk]);
      if(buffer.length>MAX+4){socket.destroy();return;}
      if(size===null&&buffer.length>=4){size=buffer.readUInt32BE(0);if(!size||size>MAX){socket.destroy();return;}}
      if(size===null||buffer.length<size+4)return;
      if(buffer.length!==size+4){socket.destroy();return;}started=true;
      let message;
      try{message=JSON.parse(buffer.subarray(4));const supplied=Buffer.from(message.token||'');
        if(supplied.length!==token.length||!timingSafeEqual(supplied,Buffer.from(token)))throw Error('Unauthorized');
      }catch{socket.destroy();return;}buffer=null;
      Promise.resolve().then(()=>handler(message.request)).then(value=>({ok:true,value}),error=>({ok:false,error:error.publicMessage||'官方远控请求失败，请重新连接并核对原操作结果。'})).then(reply=>{
        if(socket.destroyed)return;const data=Buffer.from(JSON.stringify(reply));if(data.length>MAX){socket.destroy();return;}
        const head=Buffer.alloc(4);head.writeUInt32BE(data.length);socket.end(Buffer.concat([head,data]));
      }).catch(()=>socket.destroy());
    });
  });
  await new Promise((resolve,reject)=>{server.once('error',reject);server.listen(endpoint,resolve);});
  fs.mkdirSync(profile,{recursive:true});
  const temp=record+'.'+process.pid;fs.writeFileSync(temp,JSON.stringify({endpoint,token,pid:process.pid}),{mode:0o600});fs.renameSync(temp,record);
  return ()=>{for(const peer of peers)peer.destroy();server.close();
    try{if(JSON.parse(fs.readFileSync(record,'utf8')).token===token)fs.unlinkSync(record);}catch{}
    try{fs.unlinkSync(endpoint);}catch{}try{fs.rmdirSync(directory);}catch{}
  };
}
module.exports={serveLocal};
