import assert from 'node:assert/strict';
import test from 'node:test';
import {mkdir,mkdtemp,readFile,realpath,rm,writeFile} from 'node:fs/promises';
import {join} from 'node:path';
import {apply} from '../bridge/clients/deepseek/host.mjs';

test('native metadata actions preserve cwd and never resume a cold session',async()=>{
  await mkdir('.tmp',{recursive:true});
  const folder=await mkdtemp(join('.tmp','dsh-session-actions-'));
  const cwd=await realpath(folder),other=join(cwd,'other');await mkdir(other);
  const endpoint=join(folder,'endpoint.json'),configPath=join(folder,'config.json');
  await writeFile(configPath,JSON.stringify({token:'fixture',endpoint}));
  const row={sessionId:'synthetic-session',cwd,running:false,projections:{values:{title:'Before'}}};
  const calls=[],disposers=[],agents=new Map(),sessions=new Map();let ignoreRename=false,ignoreAttach=false;
  const workspace=(id,path)=>({id,path,title:id,sessionIds:[],
    async attachSession(id){calls.push(['attach',this.id,id]);if(!ignoreAttach&&!this.sessionIds.includes(id))this.sessionIds.push(id);},
    async detachSession(id){calls.push(['detach',this.id,id]);this.sessionIds=this.sessionIds.filter(value=>value!==id);}});
  const workspaces=[workspace('same',cwd),workspace('different',other)];
  const titles={config:{maxTitleBytes:32},rename(session,title){calls.push(['rename',session.id,title]);if(!ignoreRename)row.projections.values.title=title;return {title};}};
  const forbidden=()=>{throw Error('Metadata must not activate a session');};
  const ctx={agents,sessions,sessionController:{list:async()=>({items:[row]}),rename:forbidden,create:forbidden,prompt:forbidden},
    workspaceRegistry:{list:()=>workspaces,get:id=>workspaces.find(w=>w.id===id)},
    permissionPresets:{catalog:()=>({options:[],defaultPreset:'default'})},
    get:name=>name==='sessionTitle'?titles:undefined,on(){},effect:setup=>disposers.push(setup())};
  await apply(ctx,{configPath});
  const {port}=JSON.parse(await readFile(endpoint,'utf8'));
  const request=async(action,body={})=>{
    const response=await fetch(`http://127.0.0.1:${port}/mobile`,{method:'POST',headers:{Authorization:'Bearer fixture','Content-Type':'application/json'},body:JSON.stringify({action,sid:row.sessionId,body})});
    return {status:response.status,...await response.json()};
  };
  try{
    let listing=await request('list');
    assert.equal(listing.sessions[0].capabilities.rename,false);
    assert.equal(listing.sessions[0].capabilities.organization,true);
    assert.equal((await request('rename',{title:'Cold'})).notSent,true);
    assert.equal(calls.length,0);
    sessions.set(row.sessionId,{id:row.sessionId});
    listing=await request('list');assert.equal(listing.sessions[0].capabilities.rename,true);
    const renamed=await request('rename',{title:'  新名称  '});
    assert.deepEqual(renamed.result,{id:row.sessionId,title:'新名称'});assert.equal(renamed.status,'confirmed');
    assert.equal(agents.size,0,'A native metadata change never creates an Agent');
    for(const title of [null,{},'', 'x'.repeat(121),'a\nb','a\u0000b'])assert.equal((await request('rename',{title})).status,400);
    assert.equal(calls.length,1);
    for(const title of ['two  spaces','a\u0080b','a\u200bb','测'.repeat(11)])assert.equal((await request('rename',{title})).notSent,true);
    assert.equal(calls.length,1,'Native normalization limits are checked before writing');
    delete titles.config.maxTitleBytes;
    assert.equal((await request('rename',{title:'No limit'})).notSent,true);
    titles.config.maxTitleBytes=32;
    ignoreRename=true;
    assert.match((await request('rename',{title:'Unconfirmed'})).error,/结果待核对/);
    assert.equal(calls.length,2,'A failed readback never retries the mutation');
    const info=await request('organization');
    assert.deepEqual(info.projects,[{id:'same',title:'same'}]);assert.equal(info.projectId,null);
    assert.equal((await request('organization',{operation:'set-project',projectId:'different'})).notSent,true);
    assert.equal(calls.length,2,'A permissive third-party registry cannot bypass cwd validation');
    const attached=await request('organization',{operation:'set-project',projectId:'same'});
    assert.equal(attached.status,'confirmed');assert.equal(attached.projectId,'same');
    listing=await request('list');assert.equal(listing.sessions[0].projectId,'same');assert.equal(listing.sessions[0].projectName,'same');
    await request('organization',{operation:'set-project',projectId:'same'});
    assert.equal(calls.length,3,'Already matching membership does not write again');
    const detached=await request('organization',{operation:'set-project',projectId:null});
    assert.equal(detached.status,'confirmed');assert.equal(detached.projectId,null);
    ignoreAttach=true;
    assert.match((await request('organization',{operation:'set-project',projectId:'same'})).error,/结果待核对/);
    assert.equal(calls.length,5,'Unknown attach does not replay');
    assert.equal(row.cwd,cwd);
  }finally{for(const dispose of disposers.reverse())await dispose();await rm(folder,{recursive:true,force:true});}
});

test('native context maintenance and tasks do not activate agents or expose another owner',async()=>{
 const folder=await mkdtemp(join('.tmp','dsh-maintenance-')),endpoint=join(folder,'endpoint.json'),configPath=join(folder,'config.json');
 await writeFile(configPath,JSON.stringify({token:'fixture',endpoint}));
 const row={sessionId:'parent',running:false,projections:{values:{subagentCatalog:[{id:'child',label:'Native child'}]}}};
 const calls=[],disposers=[],agent={id:'parent',status:'idle'},child={id:'child',status:'running'};
 const jobs=[{id:'own',owner:'parent',label:'Own job',status:'running'},{id:'foreign',owner:'another',status:'running'},{id:'global',status:'running'}];
 const services={commands:{list:()=>[{definitionId:'@deepseek-ai/dsh-command-compact'}],execute:async(a,line)=>{calls.push(['command',a.id,line]);return {result:{kind:'success',text:'No compactable history yet.'}};}},
   jobs:{list:()=>jobs,kill:(id,sid)=>calls.push(['kill',id,sid])},subagents:{interrupt:(id,authority)=>calls.push(['interrupt',id,authority])}};
 const ctx={agents:new Map([['parent',agent],['child',child]]),sessionController:{list:async()=>({items:[row]}),resolveAgent(){throw Error('unexpected activation');}},workspaceRegistry:{},permissionPresets:{catalog:()=>({options:[]})},get:name=>services[name],on(){},effect:setup=>disposers.push(setup())};
 await apply(ctx,{configPath});const {port}=JSON.parse(await readFile(endpoint,'utf8'));
 const request=async(action,body={})=>{const r=await fetch(`http://127.0.0.1:${port}/mobile`,{method:'POST',headers:{Authorization:'Bearer fixture'},body:JSON.stringify({action,sid:'parent',body})});return {code:r.status,...await r.json()};};
 try{
   assert.deepEqual((await request('tasks')).tasks.map(t=>t.id),['job:own','child:child']);assert.equal(calls.length,0);
   assert.equal((await request('stop-task',{taskId:'job:foreign'})).code,400);
   assert.equal((await request('stop-task',{taskId:'job:own'})).status,'accepted');
   assert.equal((await request('stop-task',{taskId:'child:child'})).status,'accepted');
   assert.deepEqual(calls[1],['interrupt','child',{kind:'user',parentSessionId:'parent'}]);
   assert.equal((await request('compact')).status,'accepted');assert.deepEqual(calls.at(-1),['command','parent','/compact']);
   row.running=true;assert.equal((await request('compact')).code,400);
   child.status='idle';assert.equal((await request('stop-task',{taskId:'child:child'})).code,400);
 }finally{for(const dispose of disposers.reverse())await dispose();await rm(folder,{recursive:true,force:true});}
});
