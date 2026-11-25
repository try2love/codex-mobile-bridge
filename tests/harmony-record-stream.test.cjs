'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const {stripTypeScriptTypes}=require('node:module');
const context=vm.createContext({});
vm.runInContext(stripTypeScriptTypes(fs.readFileSync('mobile/harmony/entry/src/main/ets/services/RecordStream.ets','utf8').replace(/^import .*;\r?\n/gm,'').replace('export class','class'))+'\nglobalThis.Reader=RecordStream;',context);
function reader(source,size=7){let at=0;return new context.Reader(async()=>at<source.length?source.slice(at,at+=size):null);}
test('Harmony transfer parser streams split escapes, Unicode and per-record body chunks',async()=>{
 const row={key:'answer',version:'v1',text:'Preview " \\ 😀',row:{role:'assistant',turnStatus:'completed'}},parts={};let length=0;
 for(let i=0;i<100;i++){const text='中😀\n\\"'.repeat(2000);parts[String(length)]={offset:length,text,next:i===99?null:length+text.length};length+=text.length;}
 const json=JSON.stringify({id:'turn',questionTime:123,messages:[{...row,bodyParts:parts}]}),read=reader(json,16383),messages=[],chunks=[],finishes=[];
 await read.read('turn',123,async value=>messages.push(value),async(_value,part)=>chunks.push(part),async(_value,characters)=>finishes.push(characters));
 assert.equal(messages.length,1);assert.equal(messages[0].text,row.text);assert.equal(chunks.length,100);assert.equal(finishes[0],length);
 assert.equal(read.buffer.length<=16383,true);assert(!Object.hasOwn(messages[0],'bodyParts'));
});
test('Harmony transfer parser rejects wrong identities, truncation and non-contiguous chunks',async()=>{
 for(const source of [JSON.stringify({id:'other',questionTime:123,messages:[]}),'{"id":"turn","questionTime":123,"messages":[',
  JSON.stringify({id:'turn',questionTime:123,messages:[{key:'k',version:'v',bodyParts:{'9':{offset:9,text:'x',next:null}}}]})])
  await assert.rejects(reader(source).read('turn',123,async()=>{},async()=>{},async()=>{}));
});
