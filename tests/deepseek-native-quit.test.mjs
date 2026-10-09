import assert from 'node:assert/strict';
import {mkdir,mkdtemp,writeFile,readFile,rm} from 'node:fs/promises';
import {join} from 'node:path';
import {apply} from '../bridge/integrations/deepseek-host.mjs';

await mkdir('.tmp',{recursive:true});
const folder=await mkdtemp(join('.tmp','dsh-native-quit-'));
const endpoint=join(folder,'endpoint.json'),configPath=join(folder,'connection.json');
await writeFile(configPath,JSON.stringify({token:'fixture-secret',endpoint}));
const services={},exits=[],disposers=[];
const ctx={sessionController:{modelCatalog:async()=>({groups:[]})},agents:{list:()=>[]},
  get:name=>services[name],on:()=>{},effect:setup=>disposers.push(setup())};
await apply(ctx,{configPath});
const {port,pid,generation}=JSON.parse(await readFile(endpoint,'utf8'));
const request=(action,body={})=>fetch(`http://127.0.0.1:${port}/mobile`,{method:'POST',
  headers:{Authorization:'Bearer fixture-secret','Content-Type':'application/json'},
  body:JSON.stringify({action,body})});
const approved={expectedPid:pid,expectedGeneration:generation};
try{
  assert.equal((await(await request('status')).json()).nativeQuit,false);
  assert.equal((await request('quit',approved)).status,400);
  services.appExit=code=>exits.push(code);
  assert.equal((await(await request('status')).json()).nativeQuit,true);
  assert.equal((await request('quit',{...approved,expectedPid:pid+1})).status,400);
  assert.equal((await request('quit',{...approved,expectedGeneration:'old-generation'})).status,400);
  ctx.agents.list=()=>[{id:'hidden-child',status:'running'}];
  assert.equal((await request('quit',approved)).status,400);
  ctx.agents.list=()=>[{id:'unknown-child'}];
  assert.equal((await request('quit',approved)).status,400);
  delete ctx.agents.list;
  assert.equal((await request('quit',approved)).status,400);
  assert.deepEqual(exits,[]);
  ctx.agents.list=()=>[{id:'idle-child',status:'idle'}];
  assert.deepEqual(await(await request('quit',approved)).json(),{status:'accepted',pid,generation});
  await new Promise(resolve=>setImmediate(resolve));
  assert.deepEqual(exits,[0]);
  console.log('PASS: DSH native quit verifies host generation and complete idle registry, then uses appExit.');
}finally{for(const dispose of disposers.reverse())await dispose();await rm(folder,{recursive:true,force:true});}
