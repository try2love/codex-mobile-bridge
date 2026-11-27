'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
function fixture(locale='zh-CN'){
 const context=vm.createContext({Date,Intl,BridgeI18n:{locale:()=>locale},document:{createElement:tag=>({tagName:tag,setAttribute(k,v){this[k]=v;}})}});
 vm.runInContext(fs.readFileSync('web/features/chat/activity.js','utf8'),context);
 return (value,now)=>{const rows=[];context.value=value;context.now=now;context.target={append:n=>rows.push(n)};vm.runInContext('renderChatMessageTime(target,value,now)',context);return rows;};
}
test('native milliseconds and ISO instants agree; local calendar dates include year only across years',()=>{
 const render=fixture(),now=new Date(2026,10,9,22),date=new Date(2026,10,9,8,7);
 const [time]=render(date.getTime(),now);assert.equal(time.textContent,'08:07');assert.equal(time.dateTime,date.toISOString());
 assert.equal(render(date.toISOString(),now)[0].textContent,time.textContent);
 assert.equal(render(new Date(2026,10,8,8,7).getTime(),now)[0].textContent,'11月8日 08:07');
 assert.equal(render(new Date(2025,10,8,8,7).getTime(),now)[0].textContent,'2025年11月8日 08:07');
 assert.ok(time.title.includes('2026'));assert.equal(time['aria-label'],time.title);
});
test('missing, invalid and ambiguous timestamps never become current time',()=>{
 const render=fixture();for(const value of [null,undefined,0,-1,NaN,Infinity,true,{},'','2026-11-09','2026-11-09T08:07:00','garbage','2026-99-99T00:00:00Z'])assert.equal(render(value).length,0);
});
test('English and timezone offsets preserve the same native instant',()=>{
 const render=fixture('en-US'),now=new Date('2026-11-09T10:00:00Z');
 assert.equal(render('2026-11-08T23:30:00-05:00',now)[0].dateTime,'2026-11-09T04:30:00.000Z');
});

test('completion uses native turn boundaries and never invents a duration or success',()=>{
 const context=vm.createContext({Date,BridgeI18n:{t:s=>s}});
 vm.runInContext(fs.readFileSync('web/features/chat/activity.js','utf8'),context);
 const label=vm.runInContext('chatTurnCompletionLabel',context),start=1700000000000;
 assert.equal(label({status:'completed',start,end:start+125000}),'已完成，用时 2 分钟 5 秒');
 assert.equal(label({status:'completed',start,end:start+999}),'已完成，用时 0 秒');
 for(const end of [undefined,null,0,'invalid',start-1])assert.equal(label({status:'completed',start,end}),'已完成');
 assert.equal(label({status:'completed',end:start+125000}),'已完成');
 assert.equal(label({status:'interrupted',start,end:start+125000}),'已停止');
 assert.equal(label({status:'failed',start,end:start+125000}),'运行失败');
 for(const status of ['inProgress','unknown','idle',undefined])assert.equal(label({status,start,end:start+125000}),'');
});

test('only a verified live turn measures elapsed time from its native start',()=>{
 const context=vm.createContext({Date,BridgeI18n:{t:s=>s}});
 vm.runInContext(fs.readFileSync('web/features/chat/activity.js','utf8'),context);
 const label=context.chatTurnCompletionLabel,start=1700000000000,turn={status:'inProgress',start,answerAt:start+10000};
 assert.equal(label(turn,{live:true,now:start+61000}),'用时 1 分钟 1 秒');
 assert.equal(label(turn,{live:true,now:start+125000}),'用时 2 分钟 5 秒');
 assert.equal(label(turn,{live:false,now:start+125000}),'用时 10 秒');
 assert.equal(label({status:'inProgress'},{live:true,now:start+125000}),'');
 assert.equal(label({status:'completed',start,end:start+4000},{live:true,now:start+125000}),'已完成，用时 4 秒');
});

test('the visible turn clock corrects immediately on foreground return and stops for removed controls',()=>{
 const events=new Map(),timers=new Map();let seq=0,paints=0;
 const on=(name,callback)=>events.set(name,callback),document={hidden:false,addEventListener:on};
 const window={BridgeChatForeground:true,addEventListener:on};
 const context=vm.createContext({document,window,Date,setTimeout:callback=>{timers.set(++seq,callback);return seq;},clearTimeout:id=>timers.delete(id)});
 vm.runInContext(fs.readFileSync('web/features/chat/activity.js','utf8'),context);
 const register=vm.runInContext('chatTurnClocks',context),node={isConnected:true};
 register(node,()=>paints++);assert.equal(timers.size,1);
 document.hidden=true;events.get('visibilitychange')();assert.equal(timers.size,0);assert.equal(paints,0);
 document.hidden=false;events.get('visibilitychange')();assert.equal(paints,1);assert.equal(timers.size,1);
 window.BridgeChatForeground=false;events.get('bridge-chat-foreground')();assert.equal(timers.size,0);
 window.BridgeChatForeground=true;events.get('bridge-chat-foreground')();assert.equal(paints,2);
 node.isConnected=false;events.get('visibilitychange')();assert.equal(timers.size,0);
});

test('Claude connector preserves native timestamp while sanitizing the transcript',()=>{
 const source=fs.readFileSync('bridge/clients/claude/connector.js','utf8');
 const sanitizer=source.slice(source.indexOf('  function sanitize('),source.indexOf('  function callArgs('));
 const sanitize=vm.runInNewContext(sanitizer+';sanitize');
 const rows=sanitize('getTranscript',[{type:'user',uuid:'u',timestamp:'2026-11-09T09:24:00Z',secret:'private',message:{role:'user',content:'Hello'}}]);
 assert.equal(rows[0].timestamp,'2026-11-09T09:24:00Z');assert.equal(rows[0].secret,undefined);
});

test('chat list time always includes HH:mm MM/dd using only a native clock',()=>{
 const ctx=vm.createContext({});vm.runInContext(fs.readFileSync('web/features/chat/activity.js','utf8'),ctx);
 const now=new Date();now.setHours(3,4,0,0);const pad=n=>String(n).padStart(2,'0');
 assert.equal(ctx.chatListTime(now.getTime()),'03:04 '+pad(now.getMonth()+1)+'/'+pad(now.getDate()));
 const previous=new Date(2020,0,2,9,8);assert.equal(ctx.chatListTime(previous.getTime()),'09:08 01/02');
 for(const absent of [null,undefined,0,true,'yesterday','2026-11-22T03:04:05'])assert.equal(ctx.chatListTime(absent),'');
});
