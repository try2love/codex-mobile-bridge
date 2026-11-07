'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const root=path.resolve(__dirname,'../..');
const host=fs.readFileSync(path.join(root,'mobile/shared/cloud/src/host.js'),'utf8');
const activity=fs.readFileSync(path.join(root,'web/features/chat/activity.js'),'utf8');
const composer=fs.readFileSync(path.join(root,'web/features/chat/composer-drafts.js'),'utf8');
function page(origin,disk){
 const local=new Map(),session=new Map();
 const storage=map=>({getItem:k=>map.get(k)??null,setItem:(k,v)=>map.set(k,v),removeItem:k=>map.delete(k)});
 class XHR{open(method,url,async){assert.equal(async,false);this.method=method;this.url=url;}setRequestHeader(k,v){this.header=[k,v];}send(raw){assert.deepEqual(this.header,['X-CMB-Cloud-View','test-grant']);this.status=200;const op=raw?JSON.parse(raw):{};if(this.method==='POST'){if(op.value===null)disk.delete(op.key);else disk.set(op.key,op.value);}this.responseText=JSON.stringify(this.method==='GET'?Object.fromEntries(disk):{});}}
 const context=vm.createContext({location:{origin,hash:'#cmb-cloud=test-grant'},history:{replaceState(){}},sessionStorage:storage(session),localStorage:storage(local),XMLHttpRequest:XHR,Request,Headers,URL,fetch:()=>{},console});
 context.window=context;vm.runInContext(host+'\n'+composer+'\n'+activity+'\nglobalThis.Drafts=ComposerDrafts;globalThis.Activity=SessionActivity;',context);
 return {drafts:new context.Drafts(),context};
}
test('cloud drafts and unknown submissions survive a different WebView origin for every provider',()=>{
 const disk=new Map();let drafts=page('http://127.0.0.1:41001',disk).drafts;
 for(const provider of ['codex','claude','deepseek','zcode']){const scope={gateway:'gateway',provider,account:'account',host:'local',session:'original'};drafts.save(scope,{text:provider+' unsent',selectionStart:2,skills:['skill']});drafts.begin(scope,{text:'possibly sent'},provider+'-request');}
 drafts=page('http://127.0.0.1:41002',disk).drafts;
 for(const provider of ['codex','claude','deepseek','zcode']){const scope={gateway:'gateway',provider,account:'account',host:'local',session:'original'};assert.equal(drafts.load(scope)?.text,provider+' unsent');assert.equal(drafts.pending(scope)?.id,provider+'-request');assert.equal(drafts.load({...scope,account:'another'}),null);}
});

test('completed unread markers survive reentry, and reading remains durable',()=>{
 const disk=new Map();let {context}=page('http://127.0.0.1:41001',disk);
 let status=new context.Activity();
 const row={host:'local',id:'thread',connected:true,status:'active',turnId:'turn',turnStatus:'inProgress'};
 status.update([row]);status.update([{...row,status:'idle',turnStatus:'completed'}]);
 assert.equal(status.indicator('local|thread')?.kind,'completed');
 context=page('http://127.0.0.1:41002',disk).context;status=new context.Activity();
 status.update([{...row,status:'idle',turnStatus:'completed'}]);
 assert.equal(status.indicator('local|thread')?.kind,'completed');
 status.read('local|thread');context=page('http://127.0.0.1:41003',disk).context;
 assert.equal(new context.Activity().indicator('local|thread'),null);
});
