'use strict';
const {test}=require('node:test'),assert=require('node:assert/strict');
const {spawnSync}=require('node:child_process'),path=require('node:path');
const script=path.join(__dirname,'../scripts/smoke-published-upgrade.cjs');
test('published-package installer refuses a daily workstation before any install or fixture write',()=>{
  const result=spawnSync(process.execPath,[script],{env:{...process.env,GITHUB_ACTIONS:'false',RUNNER_ENVIRONMENT:'github-hosted'},encoding:'utf8'});
  assert.equal(result.status,1);assert.match(result.stderr,/Only run against an ephemeral GitHub-hosted runner/);
});
test('published-package installer refuses a self-hosted runner',()=>{
  const result=spawnSync(process.execPath,[script],{env:{...process.env,GITHUB_ACTIONS:'true',RUNNER_ENVIRONMENT:'self-hosted'},encoding:'utf8'});
  assert.equal(result.status,1);assert.match(result.stderr,/Self-hosted\/daily machines are excluded/);
});
