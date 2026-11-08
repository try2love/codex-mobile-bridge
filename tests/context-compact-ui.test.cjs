'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const source=fs.readFileSync(require('node:path').join(__dirname,'../web/shell/app.js'),'utf8');
const code=source.slice(source.indexOf('const contextCompactions='),source.indexOf('let newChatProjects='));
function fixture(){
 const calls=[],messages=[];let control,serial=0,reply=async()=>({status:'accepted'});
 const context=vm.createContext({currentId:'original',currentHost:'remote',state:{id:'original',connected:true},
  chatKey:(id,host)=>host+':'+id,uuid:()=>String(++serial),t:s=>s,toast:s=>messages.push(s),$:()=>({after(){}}),
  sessionUrl:(id,action,host)=>`/${host}/${id}/${action}`,api:async(path,body)=>{calls.push({path,body});return reply();},
  ContextUsageControl:class{constructor(options){this.options=options;control=this;}update(state){this.state=state;}}});
 vm.runInContext(code,context);return {context,calls,messages,control,reply:fn=>{reply=fn;}};
}
test('compact uses selected host and shares the existing context control',async()=>{
 const f=fixture();assert.equal(await f.control.options.onCompact(),true);
 assert.equal(f.calls[0].path,'/remote/original/compact');assert.equal(f.messages[0],'已提交上下文压缩');
 await f.control.options.onCompact();assert.notEqual(f.calls[0].body.id,f.calls[1].body.id);
});
test('unknown receipt retries only the same request identity',async()=>{
 const f=fixture();f.reply(async()=>({status:'unknown',message:'请核对原客户端'}));
 assert.equal(await f.control.options.onCompact(),false);assert.equal(await f.control.options.onCompact(),false);
 assert.equal(f.calls[0].body.id,f.calls[1].body.id);assert.equal(f.messages[0],'请核对原客户端');
});
test('inflight compaction is single-flight and late reply cannot update another chat',async()=>{
 const f=fixture();let finish;f.reply(()=>new Promise(resolve=>{finish=resolve;}));
 const first=f.control.options.onCompact();assert.equal(await f.control.options.onCompact(),false);
 f.context.currentId='another';f.context.state={id:'another',connected:true};finish({status:'accepted'});await first;
 assert.equal(f.calls.length,1);assert.notEqual(f.control.state.id,'another');
});
