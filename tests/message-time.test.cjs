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

test('Claude connector preserves native timestamp while sanitizing the transcript',()=>{
 const source=fs.readFileSync('bridge/clients/claude/connector.js','utf8');
 const sanitizer=source.slice(source.indexOf('  function sanitize('),source.indexOf('  function callArgs('));
 const sanitize=vm.runInNewContext(sanitizer+';sanitize');
 const rows=sanitize('getTranscript',[{type:'user',uuid:'u',timestamp:'2026-11-09T09:24:00Z',secret:'private',message:{role:'user',content:'Hello'}}]);
 assert.equal(rows[0].timestamp,'2026-11-09T09:24:00Z');assert.equal(rows[0].secret,undefined);
});
