import assert from 'node:assert/strict';
import test from 'node:test';
import {recordLocation} from '../bridge/clients/deepseek/host.mjs';

test('record location uses the bound profile stat and never opens an agent or guesses a path',async()=>{
 const reads=[],ctx={get:name=>name==='sessionPersistence'?{stat:async id=>{reads.push(id);return {header:{id},location:{kind:'jsonl',path:'/web-profile/records/chat.jsonl'}};},locate:()=>{throw Error('Must not guess a target');},open:()=>{throw Error('Must not read the log');}}:undefined};
 assert.deepEqual(await recordLocation(ctx,'native'),{path:'/web-profile/records/chat.jsonl'});
 assert.deepEqual(reads,['native']);
});
test('pending records, unknown format and changed identity have no fabricated directory',async()=>{
 for(const row of [undefined,{header:{id:'native'}},{header:{id:'other'},location:{kind:'jsonl',path:'/other'}},{header:{id:'native'},location:{kind:'unknown',path:'/other'}}]){
  assert.deepEqual(await recordLocation({get:()=>({stat:async()=>row})},'native'),{path:null});
 }
 assert.deepEqual(await recordLocation({},'native'),{path:null});
});
