'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const context=vm.createContext({});vm.runInContext(fs.readFileSync('web/features/chat/activity.js','utf8'),context);
test('activity icons use native type or exact tool identity, never display text',()=>{
 for(const [row,icon] of [
  [{kind:'commandExecution'},'command'],[{kind:'fileChange'},'file'],[{kind:'ImageView'},'read'],
  [{kind:'reasoning'},'reasoning'],[{role:'reasoning'},'reasoning'],[{kind:'code',changeType:'edit'},'file'],
  [{tool:'Bash'},'command'],[{toolName:'bash'},'command'],[{tool:'Read'},'read'],
  [{tool:'Grep'},'search'],[{tool:'Glob'},'search'],[{tool:'WebSearch'},'search'],[{tool:'WebFetch'},'browser'],
  [{toolName:'cua_repl.js'},'computer'],[{toolName:'mcp__cua_repl__js'},'computer'],
  [{tool:'TaskUpdate'},'task'],[{kind:'todo/write'},'task'],
  [{toolName:'wait_agent'},'tool'],[{toolName:'custom_Read',title:'Read',text:'bash computer task'},'tool'],
 ])assert.equal(context.chatActivityIcon({role:'activity',...row}),icon,JSON.stringify(row));
});
test('image-bearing activity keeps the same icon on its original detail',()=>{
 const detail={classList:{contains:name=>name==='activity'},dataset:{}},row={dataset:{key:'computer'},firstElementChild:detail,__chatMessage:{role:'activity',toolName:'cua_repl.js'}};
 context.chatTaskSegments([row]);assert.equal(row.dataset.icon,'computer');assert.equal(detail.dataset.icon,'computer');
});
