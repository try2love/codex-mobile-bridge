const test=require('node:test');
const assert=require('node:assert/strict');
const {MessageManagement}=require('../web/features/chat/message-management.js');
const settle=()=>new Promise(resolve=>setImmediate(resolve));
function fixture(){
 const m=Object.create(MessageManagement.prototype),saved=[],sent=[];
 Object.assign(m,{target:{id:'chat',host:'ssh'},t:x=>x,uuid:()=> 'fresh-id',busy:false,drag:null,generation:1,dialog:{open:true},content:{inert:false},status:{textContent:''},closeButton:{},refreshButton:{},data:{version:'v1',draftAccountIdentity:'account',queue:[{id:'a'},{id:'b'},{id:'c'}]},queue:[{id:'a'},{id:'b'},{id:'c'}],render(){this.renders=(this.renders||0)+1;},refresh:async()=>{m.refreshes=(m.refreshes||0)+1;},onChanged(){},saveEditor:async(scope,editor)=>saved.push({scope,editor}),edit:async(scope,body)=>{sent.push({scope,body});return {queue:[{id:body.id,editId:body.editId,text:body.text}]};}});
 return {m,saved,sent};
}
test('up/down order is persisted with exact version and all queue IDs',async()=>{
 const {m}=fixture();m.persistOrder=async(ids,id)=>{assert.deepEqual(ids,['b','a','c']);assert.equal(id,'b');};await m.move('b',-1);
});
test('failed reorder rolls back displayed order and keeps conflict visible',async()=>{
 const {m}=fixture();m.reorder=async(scope,payload)=>{assert.deepEqual(scope,m.target);assert.deepEqual(payload,{version:'v1',expectedAccount:'account',ids:['c','a','b']});throw Error('Queue changed');};
 global.CSS={escape:x=>x};await m.persistOrder(['c','a','b'],'c');assert.deepEqual(m.data.queue.map(x=>x.id),['a','b','c']);assert.equal(m.status.textContent,'Queue changed');assert.equal(m.refreshes,undefined);assert.equal(m.busy,false);
});
function dragFixture(){
 const {m}=fixture(),handlers={};m.content={scrollTop:0,getBoundingClientRect:()=>({top:0,bottom:200})};
 const list={children:[],insertBefore(item,before){this.children.splice(this.children.indexOf(item),1);const at=before?this.children.indexOf(before):-1;this.children.splice(at<0?this.children.length:at,0,item);}};
 for(const id of ['a','b','c'])list.children.push({dataset:{id},classList:{add(){},remove(){}},getBoundingClientRect(){const index=list.children.indexOf(this);return {top:index*50,bottom:index*50+50,height:50};},get nextSibling(){return list.children[list.children.indexOf(this)+1]||null;}});
 const handle={setPointerCapture(){},hasPointerCapture:()=>true,releasePointerCapture(){},closest:()=>list.children.find(row=>row.dataset.id==='a'),addEventListener(){},removeEventListener(){}};m.list=list;m.doc={addEventListener:(name,fn)=>handlers[name]=fn,removeEventListener:name=>delete handlers[name]};
 return {m,list,handle,handlers};
}
test('pointer handle drag changes visible order and persists it only on drop',async()=>{
 const {m,list,handle,handlers}=dragFixture(),calls=[];m.persistOrder=async(ids,id)=>calls.push({ids,id});m.startDrag({button:0,pointerId:4,currentTarget:handle,preventDefault(){}},'a');
 handlers.pointermove({pointerId:4,clientY:145});assert.deepEqual(list.children.map(x=>x.dataset.id),['b','c','a']);assert.equal(calls.length,0);
 handlers.pointerup({pointerId:4,type:'pointerup'});assert.deepEqual(calls,[{ids:['b','c','a'],id:'a'}]);assert.equal(m.drag,null);assert.equal(Object.keys(handlers).length,0);
});
test('cancelled drag never persists order and refresh cannot replace drag DOM',async()=>{
 const {m,handle,handlers}=dragFixture();let calls=0;m.persistOrder=async()=>calls++;m.startDrag({button:0,pointerId:1,currentTarget:handle,preventDefault(){}},'a');m.refresh=MessageManagement.prototype.refresh;await m.refresh();handlers.pointercancel({pointerId:1,type:'pointercancel'});assert.equal(calls,0);assert.equal(m.drag,null);assert.equal(m.renders,1);
});
test('editor saves serialize immutable snapshots and unknown submission waits for prior edit save',async()=>{
 const {m,saved}=fixture();const body={id:'edit-id',status:'editing',body:{text:'one'}};m.editor=body;const a=m.persistEditor(body);body.body.text='two';const b=m.persistEditor(body);await Promise.all([a,b]);assert.deepEqual(saved.map(x=>x.editor.body.text),['one','two']);await settle();
});
test('background update never rebuilds editor, active mutation or drag',()=>{
 const {m}=fixture();let calls=0;m.refresh=()=>calls++;m.editor={};m.update();m.editor=null;m.busy=true;m.update();m.busy=false;m.drag={};m.update();m.drag=null;m.update();assert.equal(calls,1);
});
test('refresh reads local records again after receipt reconciliation',async()=>{
 const {m}=fixture();let reads=0;m.localRecords=async()=>++reads===1?[{id:'resolved'}]:[];m.load=async()=>({version:'v2',queue:[],unknown:[],receipts:[]});await MessageManagement.prototype.refresh.call(m);assert.equal(reads,2);assert.deepEqual(m.local,[]);assert.equal(m.renders,1);
});
test('ignoring grouped browser variants consumes exact snapshots only after confirmation',async()=>{
 const {m}=fixture(),records=[{id:'same',version:'old',source:'v1'},{id:'same',version:'new',source:'v2'}],seen=[];m.confirm=async()=>false;m.ignoreLocal=async(scope,record)=>seen.push(record);await m.ignoreBrowser(records);assert.deepEqual(seen,[]);m.confirm=async()=>true;await m.ignoreBrowser(records);assert.deepEqual(seen,records);assert.equal(m.refreshes,1);
});
test('a read already in flight cannot replace queue DOM after a drag starts',async()=>{
 const {m}=fixture();let finish;m.localRecords=async()=>[];m.load=()=>new Promise(resolve=>finish=resolve);const read=MessageManagement.prototype.refresh.call(m);await settle();m.drag={id:'a'};finish({queue:[],receipts:[]});await read;assert.equal(m.renders,undefined);
});
test('an older read cannot overwrite a newer queue snapshot',async()=>{
 const {m}=fixture(),finish=[];m.localRecords=async()=>[];m.load=()=>new Promise(resolve=>finish.push(resolve));const first=MessageManagement.prototype.refresh.call(m);await settle();const second=MessageManagement.prototype.refresh.call(m);await settle();finish[1]({version:'new',queue:[],receipts:[]});await second;finish[0]({version:'old',queue:[],receipts:[]});await first;assert.equal(m.data.version,'new');assert.equal(m.renders,1);
});
test('scroll interaction suppresses background refresh and discards an in-flight DOM replacement',async()=>{
 const {m}=fixture();let finish;m.scrollingUntil=Date.now()+500;let reads=0;m.refresh=()=>reads++;m.update();assert.equal(reads,0);m.scrollingUntil=0;m.localRecords=async()=>[];m.load=()=>new Promise(resolve=>finish=resolve);const request=MessageManagement.prototype.refresh.call(m);await settle();m.scrollEpoch=1;finish({version:'v2',queue:[],receipts:[]});await request;assert.equal(m.renders,undefined);
});

test('entering edit is local-only and saving updates the original ID with text only',async()=>{
 const {m,saved,sent}=fixture(),row={id:'queued-id',text:'original',attachments:['attachment'],skills:['skill'],workMode:'plan'};
 await m.editRow(row);assert.equal(sent.length,0);assert.equal(m.editor.id,'queued-id');assert.equal(m.editor.sourceId,'queued-id');assert.equal(m.editor.baseVersion,'v1');
 m.editor.body.text='edited';await m.submitEditor();assert.deepEqual(sent[0].body,{id:'queued-id',version:'v1',text:'edited',editId:'fresh-id'});
 assert.deepEqual(saved[0].editor.body.attachments,['attachment']);assert.deepEqual(saved[0].editor.body.skills,['skill']);assert.equal(saved[0].editor.body.workMode,'plan');
 assert.equal(saved[1].editor.status,'unknown');assert.equal(saved.at(-1).editor,null);assert.equal(m.editor,null);
});
test('unknown edit and dispatch race preserve original editor and never retry or enqueue',async()=>{
 for(const error of [Error('offline'),Object.assign(Error('already started'),{status:409,code:'message_started'}),Object.assign(Error('refused'),{status:400,code:'send_not_submitted'})]){
  const {m,saved}=fixture();await m.editRow({id:'original-id',text:'original'});m.editor.body.text='edited';let calls=0;m.edit=async()=>{calls++;throw error;};
  await m.submitEditor();await m.submitEditor();assert.equal(calls,1);assert.equal(m.editor.id,'original-id');assert.equal(m.editor.status,error.status===400?'editing':'unknown');assert.equal(saved.at(-1).editor.body.text,'edited');assert.equal(m.status.textContent,error.message);
 }
});
test('failed local editor persistence prevents edit mutation',async()=>{
 const {m,sent}=fixture();m.saveEditor=async()=>{throw Error('storage full');};await m.editRow({id:'queued',text:'text'});assert.equal(sent.length,0);assert.equal(m.editor,undefined);assert.equal(m.status.textContent,'storage full');
});
test('refresh confirms an unknown edit only from exact editId and edited text proof',async()=>{
 const {m,saved}=fixture();m.editor={id:'queued',sourceId:'queued',baseVersion:'v1',operation:'edit',status:'unknown',attempt:'attempt',body:{text:'new text'}};m.localRecords=async()=>[];
 for(const receipts of [[{id:'queued',status:'queued'}],[{id:'queued',status:'accepted',edit:{id:'other-attempt',text:'new text',status:'applied'}}],[{id:'queued',edit:{id:'attempt',text:'old text',status:'applied'}}]]){
  m.load=async()=>({queue:[],receipts});await MessageManagement.prototype.refresh.call(m);assert.equal(m.editor.status,'unknown');
 }
 m.load=async()=>({queue:[],receipts:[{id:'queued',status:'accepted',edit:{id:'attempt',text:'new text',status:'applied'}}]});await MessageManagement.prototype.refresh.call(m);
 assert.equal(m.editor,null);assert.equal(saved.at(-1).editor,null);
});
test('standalone withdrawal remains a single server mutation without creating an editor',async()=>{
 const {m,saved}=fixture();let calls=0;m.withdraw=async(scope,value)=>{calls++;assert.deepEqual(value,{id:'queued',version:'v1',expectedAccount:'account'});};await m.withdrawRow({id:'queued'});assert.equal(calls,1);assert.equal(saved.length,0);assert.equal(m.editor,undefined);
});

test('a definite edit refusal preserves text and requires refresh before a manual save',async()=>{
 const {m,sent}=fixture();await m.editRow({id:'queued',text:'original'});m.editor.body.text='edited';m.edit=async()=>{throw Object.assign(Error('version changed'),{status:400});};await m.submitEditor();
 assert.equal(m.editor.status,'editing');assert.equal(m.editor.blockedReason,'refreshRequired');assert.equal(m.editor.body.text,'edited');
 m.localRecords=async()=>[];m.load=async()=>({version:'v2',queue:[{id:'queued',text:'original'}],receipts:[]});await MessageManagement.prototype.refresh.call(m);
 assert.equal(m.editor.baseVersion,'v2');assert.equal(m.editor.blockedReason,null);assert.equal(sent.length,0);
});
test('refresh preserves local edit and blocks save when original text changes or dispatch wins',async()=>{
 for(const queue of [[{id:'queued',text:'changed elsewhere'}],[]]){
  const {m}=fixture();await m.editRow({id:'queued',text:'original'});m.editor.body.text='local edit';m.localRecords=async()=>[];m.load=async()=>({version:'v2',queue,receipts:[]});await MessageManagement.prototype.refresh.call(m);
  assert.equal(m.editor.body.text,'local edit');assert.equal(m.editor.blockedReason,queue.length?'conflict':'notQueued');let calls=0;m.edit=async()=>calls++;await m.submitEditor();assert.equal(calls,0);
 }
});
test('attachment-only queued messages may save an empty text in place',async()=>{
 const {m,sent}=fixture();await m.editRow({id:'queued',text:'old',attachments:['attachment']});m.editor.body.text='';await m.submitEditor();assert.equal(sent.length,1);assert.equal(sent[0].body.text,'');assert.equal(sent[0].body.id,'queued');
});

test('reopening after a failed editor save reloads current storage instead of rethrowing historical rejection',async()=>{
 const {m}=fixture(),stored={id:'current',sourceId:'current',baseVersion:'v2',operation:'edit',status:'editing',body:{text:'stored draft'}};let shown=0;
 m.editorSave=Promise.reject(Error('previous save conflict'));m.scope=()=>m.target;m.doc={activeElement:null};m.beforeOpen=()=>{};m.loadEditor=async()=>structuredClone(stored);m.dialog.showModal=()=>shown++;
 await m.open();assert.equal(shown,1);assert.deepEqual(m.editor,stored);assert.deepEqual(m.persistedEditor,stored);assert.equal(m.refreshes,1);
});
test('open, persistEditor and editRow work without structuredClone',async()=>{
 const originalClone=globalThis.structuredClone;
 globalThis.structuredClone=undefined;
 try{
  const {m,saved}=fixture();let shown=0;
  m.scope=()=>({id:'chat',host:'local'});m.doc={activeElement:null};m.beforeOpen=()=>{};m.loadEditor=async()=>undefined;m.dialog.showModal=()=>shown++;
  await m.open();assert.equal(shown,1);assert.deepEqual(m.target,{id:'chat',host:'local'});assert.equal(m.persistedEditor,undefined);
  await m.editRow({id:'queued',text:'hello'});assert.equal(m.editor.body.text,'hello');
  m.editor.body.text='mutated-after';await settle();assert.equal(saved[0].editor.body.text,'hello');
 }finally{
  globalThis.structuredClone=originalClone;
 }
});
