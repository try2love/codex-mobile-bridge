'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const source=name=>fs.readFileSync(require('node:path').join(__dirname,'../web/features/chat',name),'utf8');
function timeline(){
 const context=vm.createContext({BridgeI18n:{t:s=>s}});vm.runInContext(source('timeline.js'),context);
 const row={key:'row',version:'v',text:'preview',truncated:true};
 const stub={abort:{signal:{aborted:false}},epoch:'epoch',rows:new Map([['row',{row}]])};
 const full=vm.runInContext('ChatTimeline.prototype.fullText',context);
 return {row,stub,read:()=>full.call(stub,row)};
}
test('copy loads all detail pages and rejects version/offset changes',async()=>{
 const ui=timeline(),calls=[];
 ui.stub.read=async(action,args)=>{calls.push(args);return {key:'row',version:'v',offset:args.offset,text:args.offset?'tail':'complete',next:args.offset?null:8};};
 assert.equal(await ui.read(),'completetail');assert.deepEqual(calls.map(c=>c.offset),[0,8]);
 ui.stub.read=async()=>({key:'row',version:'new',offset:0,text:'changed',next:null});
 await assert.rejects(ui.read(),/已变化/);
 ui.stub.read=async()=>({key:'row',version:'v',offset:10,text:'missing',next:null});
 await assert.rejects(ui.read(),/已变化/);
});
test('copy of short text needs no fetch and switching chat cancels full-text result',async()=>{
 const ui=timeline();ui.row.truncated=false;ui.stub.read=()=>assert.fail('no request expected');assert.equal(await ui.read(),'preview');
 ui.row.truncated=true;ui.stub.read=async()=>{ui.stub.abort.signal.aborted=true;return {key:'row',version:'v',offset:0,text:'old',next:null};};
 await assert.rejects(ui.read(),/已变化/);
});
function fixture(){
 const nodes=new Map(),saved=new Map(),requests=[],opened=[],notices=[];
 const node=()=>({value:'',textContent:'',hidden:false,disabled:false,open:false,addEventListener(){},querySelectorAll:()=>[],showModal(){this.open=true;},close(){this.open=false;},focus(){}});
 for(const id of ['message-action-dialog','message-action-heading','message-action-description','message-action-text','message-action-error','message-action-submit','message-action-form'])nodes.set(id,node());
 const context=vm.createContext({window:{},document:{getElementById:id=>nodes.get(id)},BridgeI18n:{t:s=>s},sessionStorage:{getItem:k=>saved.get(k),setItem:(k,v)=>saved.set(k,v),removeItem:k=>saved.delete(k)}});
 vm.runInContext(source('message-actions.js'),context);
 let response={status:'unknown',id:'source',source:{id:'source'},host:'local'};
 const deps={request:async(url,body)=>{requests.push({url,body});return response;},openChat:async(...args)=>opened.push(args),refresh(){},uuid:()=> 'request-id',notify:text=>notices.push(text),busy(){}};
 context.deps=deps;const control=vm.runInContext('new MessageActions(deps)',context);
 const row={key:'row',version:'v',turnId:'turn'};const line={epoch:'epoch',abort:{signal:{aborted:false}},url:()=>'/source/message-action?host=remote',fullText:async()=> 'original'};
 return {control,nodes,saved,requests,opened,notices,row,line,setResponse:v=>response=v};
}
test('cancel sends nothing, unknown result keeps draft and retry uses same operation id',async()=>{
 const ui=fixture();await ui.control.open('edit',ui.row,ui.line);assert.equal(ui.requests.length,0);
 ui.control.input.value='edited';ui.control.input.oninput();await ui.control.send();await ui.control.send();
 assert.equal(ui.requests.length,2);assert.equal(ui.requests[0].body.id,ui.requests[1].body.id);assert.equal(ui.control.input.value,'edited');assert.equal(ui.control.dialog.open,true);
 ui.line.abort.signal.aborted=true;await ui.control.send();assert.equal(ui.requests.length,2);
});
test('successful fork opens only returned host/id and unavailable child retains edit draft',async()=>{
 const ui=fixture();await ui.control.open('edit-fork',ui.row,ui.line);
 ui.setResponse({status:'created',action:'edit-fork',id:'child',host:'remote',draft:'replacement',source:{id:'source',turnId:'turn'}});
 await ui.control.send();assert.deepEqual(ui.opened,[['child','remote']]);assert.equal(JSON.parse(ui.saved.get('fork-edit:remote|child')).text,'replacement');assert.equal(ui.control.dialog.open,false);
});
