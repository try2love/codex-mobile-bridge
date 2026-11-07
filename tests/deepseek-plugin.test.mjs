import assert from 'node:assert/strict';
import {test} from 'node:test';
import {mkdtemp,mkdir,copyFile,readFile,writeFile,rm,stat} from 'node:fs/promises';
import {resolve,join} from 'node:path';
import {pathToFileURL} from 'node:url';

test('standalone bundle preserves instance identity and refuses duplicate writers',async()=>{
  const root=await mkdtemp(resolve('.tmp/dsh-plugin-test-')),disposers=[];
  try{
    const source=resolve('bridge/clients/deepseek'),bundle=join(root,'bundle'),home=join(root,'home'),profile=join(home,'profiles/web');
    await mkdir(bundle);await mkdir(profile,{recursive:true});
    await copyFile(join(source,'plugin/index.mjs'),join(bundle,'index.mjs'));
    await copyFile(join(source,'plugin/package.json'),join(bundle,'package.json'));
    await copyFile(join(source,'plugin/cordis.patch.yml'),join(bundle,'cordis.patch.yml'));
    await copyFile(join(source,'host.mjs'),join(bundle,'host.mjs'));
    const {apply}=await import(pathToFileURL(join(bundle,'index.mjs')));
    const ctx={profileContext:{name:'web',dir:profile,home},
      sessionController:{list:async()=>({items:[]}),modelCatalog:async()=>({groups:[]})},
      sessionSkillCatalog:{},permissionPresets:{},agents:{list:()=>[]},workspaceRegistry:{},
      on:()=>{},effect:setup=>disposers.push(setup()),get:()=>undefined};
    await apply(ctx);
    const directory=join(profile,'codex-mobile-bridge');
    const json=async name=>JSON.parse(await readFile(join(directory,name),'utf8'));
    const binding=await json('binding.json'),connection=await json('connection.json'),endpoint=await json('endpoint.json');
    assert.equal(binding.profile,'web');assert.equal(binding.instanceId,connection.instanceId);
    assert.equal(binding.packageDirectory,bundle);assert.equal(connection.token.length,64);
    if(process.platform!=='win32')assert.equal((await stat(join(directory,'connection.json'))).mode&0o777,0o600);
    const response=await fetch(`http://127.0.0.1:${endpoint.port}/mobile`,{method:'POST',headers:{Authorization:'Bearer '+connection.token},body:JSON.stringify({action:'status'})});
    const status=await response.json();assert.equal(status.bridgeRevision,11);assert.deepEqual(status.plugin,binding.plugin);assert.equal(status.plugin.version,'0.2.1');
    await assert.rejects(apply(ctx),/已有 Bridge 接入/);
    assert.deepEqual(await json('endpoint.json'),endpoint);
    await disposers.pop()();
    await apply(ctx);
    assert.deepEqual(await json('binding.json'),binding);
    assert.deepEqual(await json('connection.json'),connection);
    assert.notEqual((await json('endpoint.json')).generation,endpoint.generation);
    await disposers.pop()();
    await writeFile(join(directory,'connection.json'),JSON.stringify({...connection,endpoint:'/other/endpoint.json'}),{mode:0o600});
    await assert.rejects(apply(ctx),/配置不匹配/);
    await assert.rejects(apply({...ctx,profileContext:{}}),/profileContext/);
  }finally{for(const dispose of disposers.reverse())await dispose();await rm(root,{recursive:true,force:true});}
});
