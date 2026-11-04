import assert from 'node:assert/strict';
import {mkdir,mkdtemp,readFile,realpath,rm,stat,writeFile} from 'node:fs/promises';
import {join} from 'node:path';
import {apply} from '../bridge/clients/deepseek/host.mjs';

await mkdir('.tmp',{recursive:true});
const folder=await mkdtemp(join('.tmp','dsh-project-directory-'));
const endpoint=join(folder,'endpoint.json'),configPath=join(folder,'config.json');
await writeFile(configPath,JSON.stringify({token:'project-fixture',endpoint}));
const calls=[],disposers=[],workspaces=[];
const ctx={
  sessionController:{list:async()=>({items:[]}),
    create:async value=>{calls.push(value);const workspace=workspaces.find(row=>row.id===value.workspaceId);assert.ok(workspace,'Native project creation must include workspaceId');workspace.sessionIds.push(value.sessionId);return {sessionId:value.sessionId};},
    rename:async value=>calls.push(value),
    prompt:async()=>{throw Error('Project creation must not call a model');}},
  workspaceRegistry:{list:()=>workspaces,
    resolveByPath:async path=>workspaces.find(row=>row.path===path),
    create:async path=>{const row={id:'native-workspace',path,title:'Project',sessionIds:[]};workspaces.push(row);return row;}},
  on:()=>{},effect:setup=>disposers.push(setup()),
};
await apply(ctx,{configPath});
const {port}=JSON.parse(await readFile(endpoint,'utf8'));
const request=async(action,body={})=>fetch(`http://127.0.0.1:${port}/mobile`,{
  method:'POST',headers:{Authorization:'Bearer project-fixture','Content-Type':'application/json'},
  body:JSON.stringify({action,body}),
});
try{
  assert.deepEqual((await(await request('projects')).json()).projects,[]);
  assert.equal(calls.length,0);
  const projectPath=join(folder,'new-project');await mkdir(projectPath);
  const cwd=await realpath(projectPath),identity=await stat(cwd);
  const projectDirectory={cwd,device:identity.dev,inode:identity.ino};
  const created=await request('create',{id:'explicit-project',title:'New project',projectDirectory});
  assert.equal(created.status,200);
  assert.deepEqual(calls,[{sessionId:'session-explicit-project',workspaceId:'native-workspace'},{sessionId:'session-explicit-project',title:'New project'}]);
  assert.equal(workspaces.length,1);
  assert.equal(workspaces[0].path,cwd);
  assert.deepEqual(workspaces[0].sessionIds,['session-explicit-project']);
  assert.equal((await(await request('projects')).json()).projects[0].workspaceId,'native-workspace');
  await request('create',{id:'same-project',projectDirectory});
  assert.equal(workspaces.length,1,'A second chat reuses the native workspace');
  assert.deepEqual(workspaces[0].sessionIds,['session-explicit-project','session-same-project']);
  const before=calls.length;
  // Large Windows inode numbers can round ino + 1 back to ino; use a definite mismatch.
  assert.equal((await request('create',{id:'changed-directory',projectDirectory:{...projectDirectory,inode:-1}})).status,400);
  assert.equal((await request('create',{id:'missing-directory',projectDirectory:{...projectDirectory,cwd:join(cwd,'missing')}})).status,400);
  assert.equal(calls.length,before);
}finally{
  for(const dispose of disposers.reverse())await dispose();
  await rm(folder,{recursive:true,force:true});
}
