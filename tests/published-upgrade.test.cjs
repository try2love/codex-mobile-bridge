'use strict';
const {test}=require('node:test'),assert=require('node:assert/strict');
const {spawn,spawnSync}=require('node:child_process'),path=require('node:path');
const {EventEmitter,once}=require('node:events');
const {stopOwnedGui}=require('../scripts/smoke-published-upgrade.cjs');
const script=path.join(__dirname,'../scripts/smoke-published-upgrade.cjs');
test('published-package installer refuses a daily workstation before any install or fixture write',()=>{
  const result=spawnSync(process.execPath,[script],{env:{...process.env,GITHUB_ACTIONS:'false',RUNNER_ENVIRONMENT:'github-hosted'},encoding:'utf8'});
  assert.equal(result.status,1);assert.match(result.stderr,/Only run against an ephemeral GitHub-hosted runner/);
});
test('published-package installer refuses a self-hosted runner',()=>{
  const result=spawnSync(process.execPath,[script],{env:{...process.env,GITHUB_ACTIONS:'true',RUNNER_ENVIRONMENT:'self-hosted'},encoding:'utf8'});
  assert.equal(result.status,1);assert.match(result.stderr,/Self-hosted\/daily machines are excluded/);
});
test('owned GUI signal exit completes without a numeric exit code',async()=>{
  const child=new EventEmitter();Object.assign(child,{exitCode:null,signalCode:null,killed:false});
  child.kill=()=>{child.killed=true;queueMicrotask(()=>{child.signalCode='SIGTERM';child.emit('exit',null,'SIGTERM');});return true;};
  await stopOwnedGui(child,100);
  assert.equal(child.exitCode,null);assert.equal(child.signalCode,'SIGTERM');
  child.kill=()=>{throw Error('An exited process must not be signaled again');};
  await stopOwnedGui(child,100);
});
test('a sent kill signal never substitutes for the owned GUI exit event',async()=>{
  const child=new EventEmitter();Object.assign(child,{exitCode:null,signalCode:null,killed:false});
  child.kill=()=>{child.killed=true;return true;};
  await assert.rejects(stopOwnedGui(child,20),/owned GUI exit timed out/);
  assert.equal(child.killed,true);assert.equal(child.exitCode,null);assert.equal(child.signalCode,null);
  assert.equal(child.listenerCount('exit'),0);
});
test('owned GUI shutdown observes a real spawned process exit',async()=>{
  const child=spawn(process.execPath,['-e','setInterval(()=>{},1000)'],{stdio:'ignore'});
  try{
    await once(child,'spawn');await stopOwnedGui(child,5000);
    assert.ok(child.exitCode!==null||child.signalCode!==null);
  }finally{if(child.exitCode===null&&child.signalCode===null)child.kill('SIGKILL');}
});
