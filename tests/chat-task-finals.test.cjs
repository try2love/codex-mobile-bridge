'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const context=vm.createContext({});vm.runInContext(fs.readFileSync('web/features/chat/activity.js','utf8'),context);
const node=(key,row)=>({dataset:{key},__chatMessage:{turnId:'native-turn',turnStatus:'completed',...row}});
test('an asynchronous question marked final is intermediate after the same native task continues',()=>{
 const rows=[node('input',{role:'user'}),node('question',{role:'assistant',phase:'final_answer'}),node('answer',{role:'user',interaction:'response'}),node('tool',{role:'activity'}),node('result',{role:'assistant',phase:'final_answer'})];
 const tasks=context.chatTaskSegments(rows),task=tasks.get(rows[0]);
 assert.equal(task.finals.size,1);assert.equal(task.finals.has(rows[1]),false);assert.equal(task.finals.has(rows[4]),true);
 assert.equal(tasks.get(rows[2]),task);
});
test('final replies remain separate across native turns and a partial page keeps its known final',()=>{
 const rows=[node('old',{role:'assistant',phase:'final_answer'}),node('new',{role:'assistant',phase:'final_answer',turnId:'another-turn'})];
 const tasks=context.chatTaskSegments(rows);
 assert.notEqual(tasks.get(rows[0]),tasks.get(rows[1]));
 assert.equal(tasks.get(rows[0]).finals.has(rows[0]),true);assert.equal(tasks.get(rows[1]).finals.has(rows[1]),true);
});
test('legacy history placeholder is not a native task identity',()=>{
 const rows=[node('u1',{role:'user',turnId:'history'}),node('f1',{role:'assistant',phase:'final_answer',turnId:'history'}),node('u2',{role:'user',turnId:'history'}),node('f2',{role:'assistant',phase:'final_answer',turnId:'history'})];
 const tasks=context.chatTaskSegments(rows);
 assert.notEqual(tasks.get(rows[0]),tasks.get(rows[2]));
 assert.equal(tasks.get(rows[0]).finals.has(rows[1]),true);assert.equal(tasks.get(rows[2]).finals.has(rows[3]),true);
});
