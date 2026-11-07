'use strict';
// The bundled Python holder creates the restricted Windows named pipe. It has
// no official account or chat logic; anonymous stdio dispatches to this parent.
const {spawn}=require('node:child_process');
const MAX=64*1024*1024;
const failure=()=>Error('本机控制接口暂不可用，请重试；其他网关功能不受影响。');
async function serveWindowsLocal(profile,name,handler,worker,{launch=spawn}={}){
  if(!worker?.executable)throw failure();
  const child=launch(worker.executable,[...(worker.prefix||[]),'local-service-host','--data-dir',profile,'--name',name,'--parent-pid',String(process.pid)],{stdio:['pipe','pipe','pipe'],windowsHide:true});
  let buffer=Buffer.alloc(0),stopped=false,ready=false,pending=0;
  const send=value=>{if(stopped||!child.stdin.writable)return;const data=Buffer.from(JSON.stringify(value));if(data.length>MAX)throw failure();const head=Buffer.alloc(4);head.writeUInt32BE(data.length);child.stdin.write(Buffer.concat([head,data]));};
  const stop=()=>{if(stopped)return;stopped=true;child.stdin.end();};
  stop.alive=()=>!stopped&&child.exitCode===null;stop.pid=child.pid;stop.closed=new Promise(resolve=>child.once('close',resolve));
  child.stdin.on('error',()=>{});child.stderr.on('data',()=>{});
  await new Promise((resolve,reject)=>{
    const fail=()=>{clearTimeout(timer);stop();if(!ready)reject(failure());};
    const timer=setTimeout(fail,15000);timer.unref();
    child.on('error',fail);child.on('close',fail);
    child.stdout.on('data',chunk=>{
      if(stopped)return;buffer=Buffer.concat([buffer,chunk]);
      try{
        while(buffer.length>=4){
          const size=buffer.readUInt32BE(0);if(!size||size>MAX)throw failure();if(buffer.length<size+4)break;
          const message=JSON.parse(buffer.subarray(4,size+4));buffer=buffer.subarray(size+4);
          if(!ready){if(message.type!=='ready')throw failure();ready=true;clearTimeout(timer);resolve();continue;}
          if(message.type!=='request'||!message.request||typeof message.request!=='object'||Array.isArray(message.request)||!/^[a-f0-9]{32}$/.test(message.id)||pending>=16)throw failure();
          pending++;
          Promise.resolve().then(()=>handler(message.request)).then(value=>({ok:true,value}),error=>({ok:false,error:error.publicMessage||'官方远控请求失败，请重新连接并核对原操作结果。'}))
            .then(reply=>send({id:message.id,reply})).catch(fail).finally(()=>pending--);
        }
      }catch{fail();}
    });
  });
  return stop;
}
module.exports={serveWindowsLocal};
