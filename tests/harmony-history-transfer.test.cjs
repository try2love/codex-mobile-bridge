'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),crypto=require('node:crypto');
const {stripTypeScriptTypes}=require('node:module'),{DatabaseSync}=require('node:sqlite');
const hash=data=>crypto.createHash('sha256').update(data).digest('hex');
async function fixture(){
 const sql=new DatabaseSync(':memory:');
 const context=vm.createContext({Uint8Array,Promise,GatewayHttp:class{},util:{
  Base64Helper:class{encodeToStringSync(data){return Buffer.from(data).toString('base64');}decodeSync(data){return new Uint8Array(Buffer.from(data,'base64'));}},
  TextDecoder:{create(){const decoder=new TextDecoder('utf-8',{fatal:true});return {decodeToString:(data,options)=>decoder.decode(data,options)};}}
 },cryptoFramework:{createMd(){const digest=crypto.createHash('sha256');return {update:async({data})=>digest.update(data),digest:async()=>({data:digest.digest()})};}}});
 for(const name of ['OfflineArchive','RecordStream','Digest','HistoryTransfer']){
  const source=fs.readFileSync('mobile/harmony/entry/src/main/ets/services/'+name+'.ets','utf8').replace(/^import .*;\r?\n/gm,'').replaceAll('export class','class').replaceAll('export type','type');
  vm.runInContext(stripTypeScriptTypes(source)+'\nglobalThis.'+name+'='+name+';',context);
 }
 const db={run:async(query,values=[])=>{sql.prepare(query).run(...values);},rows:async(query,values=[])=>sql.prepare(query).all(...values).map(Object.values)};
 const archive=new context.OfflineArchive(db);await archive.init();const account=await archive.bind('https://gateway.example','Fixture','a'.repeat(32),'b'.repeat(64));
 const network={available:true,unmetered:true,epoch:'fixture-network'},transfer=new context.HistoryTransfer(archive,network,()=>1024*1024*1024);
 return {context,sql,archive,account,network,transfer,close:()=>sql.close(),command:(type,event)=>archive.serial(()=>transfer.command(account,type,event))};
}
function record(text='Saved 😀 content'){
 const message={key:'answer',version:'v1',role:'assistant',text:text.slice(0,6000),order:1,row:{role:'assistant',turnId:'turn',truncated:text.length>6000,turnStatus:'completed',phase:'final_answer'}},parts={};
 if(text.length>6000)for(let offset=0;offset<text.length;offset+=16000){const end=Math.min(text.length,offset+16000);parts[offset]={offset,text:text.slice(offset,end),next:end<text.length?end:null};}
 const data=Buffer.from(JSON.stringify({id:'turn',questionTime:123,messages:[{...message,bodyParts:parts}]}));
 const event={sessionId:'["codex","local","session"]',turnId:'turn',questionTime:123,clearRevision:'0',taskClearRevision:'0',version:hash(data),bytes:data.length,
  kind:'record',key:'answer',recordVersion:'v1',textLength:text.length,title:'Saved transfer',syncedAt:200};
 return {data,event,text};
}
async function stage(f,data,event){
 for(let index=0,offset=0;offset<data.length;index++,offset+=524288){const block=data.subarray(offset,offset+524288);const result=await f.command('transferChunkPut',{...event,index,data:block.toString('base64'),sha256:hash(block)});assert.equal(result.saved,true);}
}
test('Harmony transfers verify native chunks and stream long Unicode records into indexed offline pages',async()=>{
 const f=await fixture();try{
  const r=record('中文😀'.repeat(240000));await stage(f,r.data,r.event);
  assert.equal((await f.transfer.commit(f.account,r.event,false,()=>true)).saved,true);
  assert.equal((await f.command('transferCached',r.event)).present,true);
  assert.equal(f.sql.prepare('SELECT COUNT(*) AS n FROM chunks').get().n,0,'committed text does not retain a second raw copy');
  assert.equal((await f.command('transferSessionComplete',r.event)).complete,true);
  const page=await f.archive.page(f.account,r.event.sessionId,{});assert.equal(page.gaps,false);assert(page.rows[0].text.length<=6000);
  let output='',offset=0;
  do{const part=await f.archive.page(f.account,r.event.sessionId,{mode:'body',key:'answer',version:'v1',offset});output+=part.text;offset=part.next;}while(offset!==null);
  assert.equal(output,r.text);
 }finally{f.close();}
});
test('Harmony transfer admission rejects changed data, cleared dates and switched network overrides',async()=>{
 const f=await fixture();try{
  const r=record(),event={...r.event,index:0,offset:0,url:'/api/history-transfer/content?id='+'a'.repeat(32)+'&item='+'b'.repeat(64)+'&offset=0&length='+r.data.length};
  assert.equal((await f.command('transferChunkPut',{...event,data:r.data.toString('base64'),sha256:'0'.repeat(64)})).reason,'integrity');
  f.network.unmetered=false;
  assert.equal((await f.transfer.fetch(f.account,event,'https://gateway.example','cookie',()=>true)).reason,'network');
  assert.equal((await f.transfer.fetch(f.account,{...event,allowMetered:true,networkEpoch:'old-network'},'https://gateway.example','cookie',()=>true)).reason,'network');
  f.network.unmetered=true;f.transfer.http.request=async()=>({status:206,headers:{'Content-Range':'bytes 0-'+(r.data.length-1)+'/'+r.data.length,ETag:'"sha256-'+r.event.version+'"','X-Chunk-SHA256':hash(r.data)},data:new Uint8Array(r.data)});
  assert.equal((await f.transfer.fetch(f.account,event,'https://gateway.example','cookie',()=>true)).saved,true);
  await f.archive.clear(f.account,0,200);
  assert.equal((await f.command('transferCached',r.event)).cleared,true);
  assert.equal((await f.transfer.commit(f.account,r.event,false,()=>true)).reason,'cleared');
 }finally{f.close();}
});
test('Harmony transfer persistence honors dismissed tasks and stops work when the selected document changes',async()=>{
 const f=await fixture();try{
  await f.command('transferTaskSave',{task:{state:'completed',dismissed:true,manifestId:'fixture'}});
  assert.equal((await f.command('transferTaskLoad',{})).task.dismissed,true);
  const r=record();await stage(f,r.data,r.event);
  assert.equal((await f.transfer.commit(f.account,r.event,false,()=>false)).reason,'permission');
  assert.equal(f.sql.prepare('SELECT COUNT(*) AS n FROM records').get().n,0);
 }finally{f.close();}
});
test('Harmony keeps identical attachment bytes reusable while binding every attachment alias separately',async()=>{
 const f=await fixture();try{
  const data=Buffer.from('identical attachment bytes'),event={...record().event,kind:'attachment',version:hash(data),bytes:data.length,mediaType:'text/plain',attachmentKey:'first-file'};
  await stage(f,data,event);assert.equal((await f.transfer.commit(f.account,event,true,()=>true)).saved,true);
  const second={...event,attachmentKey:'second-file'};
  const pending=await f.command('transferCached',second);assert.equal(pending.present,false);assert.equal(pending.missingBytes,0);
  assert.equal((await f.transfer.commit(f.account,second,true,()=>true)).saved,true);
  for(const attachmentKey of ['first-file','second-file']){
   const meta=await f.command('transferAttachment',{sessionId:event.sessionId,turnId:event.turnId,attachmentKey});assert.equal(meta.version,event.version);
   const part=await f.command('transferAttachment',{sessionId:event.sessionId,turnId:event.turnId,version:meta.version,index:0});assert.equal(Buffer.from(part.data,'base64').toString(),data.toString());
  }
  assert.equal((await f.command('transferAttachment',{sessionId:'another-session',turnId:event.turnId,version:event.version})).version,undefined);
  await f.archive.clear(f.account,0,200);
  assert.equal(f.sql.prepare('SELECT COUNT(*) AS n FROM attachment_keys').get().n,0);
  assert.equal(f.sql.prepare('SELECT COUNT(*) AS n FROM chunks').get().n,0);
 }finally{f.close();}
});
test('an older Harmony transfer cannot overwrite the completion proof of newer live content',async()=>{
 const f=await fixture();try{
  const r=record('older transferred text'.repeat(1000));await stage(f,r.data,r.event);
  const text='newer live text'.repeat(2000);
  await f.archive.merge(f.account,{sessionId:r.event.sessionId,syncedAt:300,source:'live',clearRevision:'0',turns:[{id:'turn',questionTime:123,
   messages:[{key:'answer',version:'v2',role:'assistant',text,order:1,row:{role:'assistant'}}]}]});
  assert.equal((await f.transfer.commit(f.account,r.event,false,()=>true)).saved,true);
  const proof=f.sql.prepare('SELECT version,length FROM finished_records').get();assert.equal(proof.version,'v2');assert.equal(proof.length,text.length);
  assert.equal(await f.archive.hasGaps(f.account,r.event.sessionId),false);
 }finally{f.close();}
});
test('Harmony offline export acknowledges the picker promptly and rechecks clearing before any external copy',async()=>{
 const f=await fixture();try{
  const data=Buffer.from('fixture export'),event={...record().event,kind:'attachment',version:hash(data),bytes:data.length,mediaType:'text/plain',attachmentKey:'file'};
  await stage(f,data,event);await f.transfer.commit(f.account,event,true,()=>true);
  let selected,serial=0,copies=0;const files=new Map();
  f.context.util.generateRandomUUID=()=>String(++serial);f.context.AttachmentDownload={filename:()=> 'fixture.txt'};
  f.context.fileIo={OpenMode:{CREATE:1,READ_WRITE:2,TRUNC:4},openSync:path=>{const file={fd:++serial,path};files.set(file.fd,file);return file;},
   writeSync:(_fd,buffer)=>buffer.byteLength,closeSync:()=>{},copyFileSync:()=>{copies++;},unlinkSync:path=>{for(const [id,file] of files)if(file.path===path)files.delete(id);}};
  f.context.picker={DocumentSaveOptions:class{},DocumentViewPicker:class{save(){return new Promise(resolve=>selected=resolve);}}};
  const result=await f.transfer.exportAttachment({cacheDir:'/private-cache'},f.account,{sessionId:event.sessionId,turnId:event.turnId,attachmentKey:'file',name:'fixture.txt'},()=>true);
  assert.equal(result.opened,true);assert.equal(typeof selected,'function');assert.equal(copies,0);
  await f.archive.clear(f.account,0,200);selected(['user-selected-document']);await new Promise(setImmediate);
  assert.equal(copies,0);assert.equal(files.size,0,'the temporary export is removed after the picker completes');
 }finally{f.close();}
});
test('the shared history transfer task completes against the production Harmony protocol and SQLite archive',async()=>{
 const f=await fixture();try{
  const HistoryTransferTask=require('../web/features/chat/history-transfer-task.js'),r=record('Shared pipeline 中文😀'.repeat(5000));
  const attachment=Buffer.from('attachment from the common transfer task'),session={title:'Shared fixture'};
  const recordItem={...r.event,cacheSessionId:r.event.sessionId,id:'a'.repeat(64),session,mime:'application/json',chunkBytes:524288};
  const attachmentItem={...r.event,cacheSessionId:r.event.sessionId,id:'b'.repeat(64),kind:'attachment',attachmentKey:'attached-file',version:hash(attachment),bytes:attachment.length,mime:'text/plain',session,chunkBytes:524288};
  f.transfer.http.request=async(_base,route)=>{
   const query=new URL('https://gateway.example'+route).searchParams,data=query.get('item')===recordItem.id?r.data:attachment;
   const offset=Number(query.get('offset')),length=Number(query.get('length')),part=data.subarray(offset,offset+length);
   return {status:206,headers:{'Content-Range':'bytes '+offset+'-'+(offset+length-1)+'/'+data.length,ETag:'"sha256-'+hash(data)+'"','X-Chunk-SHA256':hash(part)},data:new Uint8Array(part)};
  };
  const manager=new HistoryTransferTask({request:async()=>({status:'ready',items:[recordItem,attachmentItem]}),delay:async()=>{},native:async(type,event={})=>{
   if(type==='transferChunkFetch')return f.transfer.fetch(f.account,event,'https://gateway.example','fixture-cookie',()=>true);
   if(type==='transferRecordCommit'||type==='transferAttachmentCommit')return f.transfer.commit(f.account,event,type==='transferAttachmentCommit',()=>true);
   return f.command(type,event);
  }});
  await manager.start({id:'c'.repeat(32),capturedAt:200,requestedAt:200,downloadBytes:r.data.length+attachment.length,fullBytes:r.data.length+attachment.length,reusedBytes:0});
  for(let turn=0;manager.running&&turn<200;turn++)await new Promise(setImmediate);
  assert.equal(manager.task.state,'completed',manager.task.error);assert.equal(manager.task.completedItems,2);
  const page=await f.archive.page(f.account,r.event.sessionId,{});assert.equal(page.title,'Shared fixture');assert.equal(page.gaps,false);
  const meta=await f.command('transferAttachment',{sessionId:r.event.sessionId,turnId:r.event.turnId,attachmentKey:'attached-file'});assert.equal(meta.version,attachmentItem.version);
  await manager.dismiss();assert.equal((await f.command('transferTaskLoad',{})).task.dismissed,true);
 }finally{f.close();}
});
