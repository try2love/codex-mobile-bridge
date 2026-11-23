const test=require('node:test'),assert=require('node:assert/strict');
const fs=require('node:fs'),vm=require('node:vm');

async function fixture(){
 const stored=new Map(),context=vm.createContext({window:{addEventListener(){},dispatchEvent(){}},
   sessionStorage:{getItem:key=>stored.get(key)||null,setItem:(key,value)=>stored.set(key,value),removeItem:key=>stored.delete(key)},
   URLSearchParams,TextEncoder,crypto:require('node:crypto').webcrypto,BridgeI18n:{t:value=>value}});
 for(const file of ['sync-cache.js','sync-transport.js'])vm.runInContext(fs.readFileSync('web/features/chat/'+file,'utf8'),context);
 const cache=context.window.BridgeChatSyncCache;
 await cache.bind({sessionSync:{version:1,gatewayId:'a'.repeat(32),identity:'b'.repeat(64)},listCacheIdentity:'fixture'});
 const scope=cache.scope('claude','local','chat');
 const row={key:'answer',version:'content',turnId:'turn',role:'assistant',text:'Open [report](/audit/report.md)',order:0};
 const files=[{id:'file',reference:'/audit/report.md',references:[],scope:'host',path:'/audit/report.md'}];
 const page={version:'page',rows:[row],index:[row],files,meta:{},turns:[]};
 await cache.write(scope,'recent',{},page);
 return {cache,scope,row,files,page,read:context.window.BridgeChatSync.read};
}

test('authorized metadata refresh restores file actions without downloading the cached message again',async()=>{
 const f=await fixture(),known=await f.cache.known(f.scope,'recent',{});
 assert.equal((await f.cache.read(f.scope,'recent',{})).files.length,0);
 const result=await f.cache.write(f.scope,'recent',{}, {...f.page,rows:[],notModified:true},{validation:known});
 assert.equal(result.rows[0].text,f.row.text);
 assert.equal(result.files[0].path,'/audit/report.md');
});

test('body reuse retains file metadata and upgrades older cached chunks once',async()=>{
 const f=await fixture(),body={key:f.row.key,version:f.row.version,offset:0,text:f.row.text,next:null};
 await f.cache.write(f.scope,'body',{key:body.key,offset:0},body);
 let requests=0,authorized=true;
 const target={scope:f.scope,url:'/fixture/sync',authorized:()=>authorized,request:async()=>{requests++;return {...body,files:f.files};}};
 const params={key:body.key,version:body.version,offset:0};
 const first=await f.read(target,'body',params,'manual',()=>true);
 const next=await f.read(target,'body',params,'manual',()=>true);
 assert.equal(requests,1);
 assert.equal(first.files[0].path,next.files[0].path);
 authorized=false;
 assert.equal(await f.read(target,'body',params,'manual',()=>true),null);
 assert.equal(requests,1);
});
