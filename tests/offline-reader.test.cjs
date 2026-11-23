'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
function fixture(){
  const window={},document={documentElement:{hasAttribute:()=>false,dataset:{}},dispatchEvent(){}};
  class CustomEvent{constructor(type,options){this.type=type;this.detail=options?.detail;}}
  const context=vm.createContext({window,document,CustomEvent,URL,URLSearchParams,setTimeout,clearTimeout});
  vm.runInContext(fs.readFileSync('web/features/chat/offline-reader/offline-reader.js','utf8'),context);
  const archiveId=JSON.stringify(['codex','local','saved']),calls=[],long='Full reasoning 🚀'.repeat(2000);
  const message=(key,role,order,fields={})=>({key,version:key+'-v',role,order,text:key,row:{key,version:key+'-v',role,order,turnId:'turn',turnStatus:'completed',turnCompletionKnown:true,turnStartedAt:1000,turnCompletedAt:2000,...fields}});
  const entries=[
    message('question','user',0),
    message('process-stable','activity',.5,{kind:'processPage',processCursor:'archive-epoch.process-stable'}),
    {...message('thought','reasoning',1,{kind:'reasoning',truncated:true}),bodyParts:{0:{offset:0,text:long,next:null}}},
    message('response','user',2,{interaction:'response'}),
    message('tool','activity',3,{kind:'commandExecution'}),
    message('answer','assistant',4,{forkable:true}),
  ];
  const reader=window.OfflineChatReader;
  reader.enableHot(async(action,fields)=>{
    calls.push({action,...fields});
    if(action==='list')return {sessions:[{id:archiveId,title:'Saved complete chat',syncedAt:2000}]};
    if(action==='session')return {id:archiveId,title:'Saved complete chat',turns:[{id:'turn',content:{messages:Object.fromEntries(entries.map(row=>[row.key,row]))}}],before:null};
    throw Error('Unexpected native request '+action);
  });
  const request=(mode,params={})=>reader.request('/api/sessions/saved/sync?'+new URLSearchParams({mode,...params}));
  return {reader,request,calls,long,archiveId};
}
test('transferred process can be read using an earlier online timeline epoch without network',async()=>{
  const f=fixture();const page=await f.request('recent');
  assert.equal(page.hasMore,false);
  const process=await f.request('process',{key:'online-epoch.process-stable'});
  assert.deepEqual(Array.from(process.rows,row=>row.key),['thought','response','tool']);
  assert.equal(process.rows[0].text,f.long);
  assert.equal(process.hasMore,false);
  assert(f.calls.every(call=>['list','session'].includes(call.action)));
});
test('offline long-body lookup preserves native version and offsets',async()=>{
  const f=fixture();await f.request('recent');
  const part=await f.request('body',{key:'online-epoch.thought',version:'thought-v',offset:0});
  assert.equal(part.text,f.long);assert.equal(part.next,null);
  await assert.rejects(f.request('body',{key:'online-epoch.thought',version:'changed',offset:0}));
});
test('offline final-answer presentation survives removing write capabilities',async()=>{
  const f=fixture();const page=await f.request('recent'),answer=page.rows.find(row=>row.key==='answer');
  assert.equal(answer.phase,'final_answer');assert.equal(answer.forkable,false);
});
test('missing offline process is reported rather than silently returned as complete',async()=>{
  const f=fixture();await f.request('recent');await assert.rejects(f.request('process',{key:'other-epoch.missing'}));
});


test('message pages keep first screen bounded and fetch process/body pages on demand',async()=>{
 const f=fixture(),reader=f.reader;
 const first={recordPage:1,id:f.archiveId,title:'Record pages',syncedAt:2000,rows:[
  {key:'question',version:'v',turnId:'turn',role:'user',order:0,text:'Question'},
  {key:'answer',version:'v',turnId:'turn',role:'assistant',phase:'final_answer',order:2,text:'Preview',truncated:true}
 ],turns:[{id:'turn',processes:[{key:'group',version:'v',order:1,processCursor:'group'}]}],before:'older',hasMore:true};
 const calls=[];reader.enableHot(async(action,input)=>{
  calls.push({action,...input});
  if(action==='list')return {sessions:[{id:f.archiveId,title:'Record pages'}]};
  if(input.mode==='process')return {...first,rows:[{key:'tool',version:'v',turnId:'turn',role:'activity',order:0,text:'Tool'}],next:input.after?null:'next',hasMore:!input.after};
  if(input.mode==='body')return {key:'answer',version:'v',offset:0,text:'Full answer',next:null};
  if(input.before)return {...first,rows:[{key:'older',version:'v',turnId:'old',role:'user',order:0,text:'Older'}],turns:[],before:null};
  return first;
 });
 const page=await f.request('recent');assert.equal(page.rows.length,2);assert.equal(calls.length,2);
 assert.equal(page.rows[1].truncated,true);assert.equal(page.rows[1].forkable,false);assert.equal(page.rows[1].detailCursor,'answer');
 const process=await f.request('process',{key:'online.group'});assert.equal(process.next,'next');
 assert.equal(process.rows[0].key,'tool');
 await f.request('process',{key:'online.group',after:process.next});
 assert.equal(calls.at(-1).after,'next');
 const body=await f.request('body',{key:'epoch.answer',version:'v',offset:0});assert.equal(body.text,'Full answer');
 const older=await f.request('older',{before:'older'});assert(older.rows[0].order<page.rows[0].order);
});
