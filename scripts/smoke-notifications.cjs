'use strict';
// Exercise each packaged notification channel against a loopback-only server.
// All credentials and messages are synthetic; no real phone receives a push.
const assert=require('node:assert/strict'),fs=require('node:fs/promises'),path=require('node:path'),http=require('node:http');
const {runWorker}=require('../desktop/shared/controller.cjs');
const root=path.resolve(__dirname,'..');
async function main(){
  assert.ok(['darwin','win32','linux'].includes(process.platform));
  if(process.platform==='linux')assert.ok(process.argv[2],'Pass the packaged Linux gateway path');
  const executable=process.argv[2]||path.join(root,'dist/desktop',process.platform==='darwin'?
    `${process.arch==='x64'?'mac':'mac-'+process.arch}/Codex Mobile Bridge.app/Contents/Resources/gateway/codex-mobile-gateway`:
    'win-unpacked/resources/gateway/codex-mobile-gateway.exe');
  await fs.mkdir(path.join(root,'.tmp'),{recursive:true});
  const dataDir=await fs.mkdtemp(path.join(root,'.tmp','notifications-中文 '));
  const requests=[];let rejectBark=false;
  const server=http.createServer(async(request,response)=>{
    try{
      const chunks=[];for await(const chunk of request)chunks.push(chunk);
      requests.push({url:request.url,authorization:request.headers.authorization,payload:JSON.parse(Buffer.concat(chunks).toString('utf8'))});
      response.writeHead(200,{'Content-Type':'application/json'});
      response.end(JSON.stringify(request.url==='/bark/push'?{code:rejectBark?400:200,message:rejectBark?'synthetic-bark-key':'success'}:{}));
    }catch{response.writeHead(400);response.end();}
  });
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  const base='http://127.0.0.1:'+server.address().port,worker=(action,payload)=>runWorker({executable,dataDir},action,payload);
  try{
    let state=await worker('snapshot');
    Object.assign(state.preferences,{codexHome:dataDir,autoStart:false,lan:false,tunnel:false,connections:[]});
    Object.assign(state.notifications,{enabled:false,topic:'',barkEnabled:true,barkServer:base+'/bark',barkKey:'synthetic-bark-key'});
    state=await worker('save',state);
    assert.equal(state.notifications.enabled,false);assert.equal(state.notifications.barkEnabled,true);
    assert.equal(state.notifications.barkKey,'');assert.equal(state.notifications.hasBarkKey,true);
    assert.equal(state.runtime.running,false);
    assert.ok((await worker('test-notification',{channel:'bark'})).message.startsWith('Bark'));
    assert.equal(requests.length,1);assert.equal(requests[0].url,'/bark/push');
    assert.equal(requests[0].payload.device_key,'synthetic-bark-key');
    assert.equal(requests[0].payload.title,'Codex 手机通知测试');
    assert.equal(requests[0].payload.group,'Codex Mobile Bridge');
    Object.assign(state.notifications,{enabled:true,server:base+'/ntfy',topic:'synthetic-topic',token:'synthetic-ntfy-token'});
    state=await worker('save',state); // Blank Bark key must preserve the saved secret.
    assert.equal(state.notifications.token,'');assert.equal(state.notifications.hasToken,true);
    assert.equal(state.notifications.barkEnabled,true);assert.equal(state.notifications.hasBarkKey,true);
    await worker('test-notification',{channel:'ntfy'});
    assert.equal(requests[1].url,'/ntfy/');assert.equal(requests[1].authorization,'Bearer synthetic-ntfy-token');
    assert.equal(requests[1].payload.topic,'synthetic-topic');
    await worker('test-notification',{channel:'bark'});
    assert.equal(requests[2].payload.device_key,'synthetic-bark-key');
    rejectBark=true;
    await assert.rejects(worker('test-notification',{channel:'bark'}),error=>error.message.startsWith('Bark ')&&!error.message.includes('synthetic-bark-key'));
    await worker('test-notification',{channel:'ntfy'});
    assert.equal(requests[4].url,'/ntfy/');
    state=await worker('snapshot');
    assert.equal(state.runtime.running,false);
    assert.equal(JSON.stringify(state).includes('synthetic-bark-key'),false);
    assert.equal(JSON.stringify(state).includes('synthetic-ntfy-token'),false);
    console.log('PASS: packaged Bark-only setup, independent Bark/ntfy test delivery, Unicode payload, secret masking, blank-key preservation and rejected-response handling.');
  }finally{
    server.closeAllConnections();await new Promise(resolve=>server.close(resolve));
    await fs.rm(dataDir,{recursive:true,force:true});
  }
}
main().catch(error=>{console.error(error);process.exitCode=1;});
