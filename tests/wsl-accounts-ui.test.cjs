'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
class Node{
  constructor(tag){this.tagName=tag;this.children=[];this.value='';this.dataset={};this.classList={add(){}};}
  append(...nodes){this.children.push(...nodes);}prepend(...nodes){this.children.unshift(...nodes);}replaceChildren(...nodes){this.children=nodes;}setAttribute(){}remove(){}focus(){}
}
const source=fs.readFileSync('web/features/accounts/wsl-accounts.js','utf8');
function panel({read=async entry=>snapshot(entry),request=async()=>({}),desktop=false,onSwitched,provider='codex'}={}){const ctx={document:{createElement:tag=>new Node(tag),addEventListener(){},removeEventListener(){}},BridgeI18n:{t:key=>key,locale:()=> 'en'},Date};vm.createContext(ctx);vm.runInContext(source+'\nthis.Panel=WslAccountsPanel',ctx);return new ctx.Panel({root:new Node('div'),provider,read,request,desktop,onSwitched});}
function snapshot(entry){return{provider:'codex',entry,identity:'identity-'+entry,target:{distribution:'Ubuntu',user:'user',configDir:'/home/user/.codex'},available:true,blockers:[],capabilities:{save:true,import:true,switch:true,models:true},accounts:[{id:'saved',kind:'api',name:'Saved'}]};}
function all(node){return[node,...node.children.flatMap(child=>child instanceof Node?all(child):[])];}
test('a late previous-environment snapshot cannot overwrite the selected scope',async()=>{let resolve;const p=panel({read:entry=>entry==='old'?new Promise(done=>resolve=done):Promise.resolve(snapshot(entry))});const old=p.select('old');await p.select('new');resolve(snapshot('old'));await old;assert.equal(p.entry,'new');assert.equal(p.value.entry,'new');});
test('clear invalidates pending mutation and clears API fields',async()=>{let resolve;const sent=[];const p=panel({desktop:true,request:value=>{sent.push(value);return new Promise(done=>resolve=done);}});await p.select('old');p.key.value='secret';const pending=p.perform('save-api',{apiKey:p.key.value});assert.equal(p.key.value,'');p.select('new');resolve(snapshot('old'));await pending;assert.equal(p.entry,'new');assert.equal(p.value.entry,'new');assert.equal(sent[0].apiKey,'');});
test('authenticated web exposes credential enrollment alongside saved switches and models',async()=>{const p=panel();await p.select('wsl');const nodes=all(p.root);assert.equal(nodes.some(node=>node.tagName==='input'),true);assert.equal(nodes.some(node=>node.textContent==='切换到此接入'),true);assert.equal(nodes.some(node=>node.textContent==='查看上游模型'),true);});
test('models are queried only by explicit action and retain timestamp cache',async()=>{const calls=[];const p=panel({request:async value=>{calls.push({...value});return{models:[{id:'model-a'}],cache:{state:'fresh',updatedAt:100}};}});await p.select('wsl');assert.equal(calls.length,0);await p.perform('models',{id:'saved'});p.render();assert.equal(calls.length,1);assert.equal(calls[0].entry,'wsl');assert.equal(Object.hasOwn(calls[0],'expectedIdentity'),false);assert.equal(p.models.get('saved').cache.updatedAt,100);});
test('unavailable environment cannot mutate and mismatched snapshot is rejected',async()=>{let writes=0;const p=panel({read:async entry=>({...snapshot(entry),available:false}),request:async()=>{writes++;}});await p.select('wsl');await p.perform('switch',{id:'saved'});assert.equal(writes,0);const mismatch=panel({read:async()=>snapshot('other')});await mismatch.select('wsl');assert.equal(mismatch.value,null);assert.match(mismatch.error,/不一致/);});
test('desktop import explicitly lists public sources and binds selected identity',async()=>{const calls=[];const p=panel({desktop:true,request:async value=>{calls.push({...value});return value.action==='import-sources'?{sources:[{id:'source-a',name:'Office API',source:'Desktop',sourceIdentity:'source-digest'}]}:snapshot('wsl');}});await p.select('wsl');assert.equal(calls.length,0);await p.perform('import-sources');assert.equal(p.sources[0].sourceIdentity,'source-digest');const select=all(p.root).find(node=>node.tagName==='select'&&node.children.some(option=>option.value==='source-a'));assert.ok(select);select.value='source-a';const form=all(p.root).find(node=>node.tagName==='form'&&node.children.some(child=>child.children?.includes(select)));assert.ok(form);form.onsubmit({preventDefault(){}});await Promise.resolve();assert.equal(calls.at(-1).action,'import-api');assert.equal(calls.at(-1).sourceId,'source-a');assert.equal(calls.at(-1).sourceIdentity,'source-digest');});
test('native snapshot is restored by switch and never renamed or deleted',async()=>{const p=panel({desktop:true,read:async entry=>({...snapshot(entry),accounts:[{id:'native',kind:'native',name:'Original login'}]})});await p.select('wsl');const texts=all(p.root).map(node=>node.textContent);assert.equal(texts.includes('重命名'),false);assert.equal(texts.includes('删除'),false);assert.equal(texts.includes('保存当前原生接入'),false);assert.equal(texts.includes('切换到此接入'),true);});
test('model requests match the strict public HTTP schema while switches bind identity',async()=>{const calls=[];const allowed=new Set(['provider','entry','action','id','refresh']);const p=panel({request:async value=>{if(value.action==='models'){for(const key of Object.keys(value))assert.ok(allowed.has(key),'unexpected public models field '+key);calls.push({...value});return{provider:'codex',entry:'wsl',models:[]};}assert.equal(value.expectedIdentity,'identity-wsl');calls.push({...value});return snapshot('wsl');}});await p.select('wsl');await p.perform('models',{id:'saved',refresh:true});assert.deepEqual(Object.keys(calls[0]).sort(),['action','entry','id','provider','refresh']);await p.perform('switch',{id:'saved'});assert.equal(calls[1].expectedIdentity,'identity-wsl');});
test('native access explains original CLI models without an unsupported model action',async()=>{const p=panel({read:async entry=>({...snapshot(entry),accounts:[{id:'native',kind:'native',name:'Original login'}]})});await p.select('wsl');const nodes=all(p.root);assert.equal(nodes.some(node=>node.tagName==='button'&&node.textContent==='查看上游模型'),false);assert.ok(nodes.some(node=>node.textContent==='原生登录的模型请在原 CLI 查看；已有聊天继续使用原模型。'));});
test('model details preserve upstream names and reported efforts without inventing missing levels',async()=>{const p=panel({request:async()=>({models:[{id:'model-id',name:'Reasoning Model',efforts:['low',{id:'high',name:'High'}]},{id:'unknown-effort'}],cache:{updatedAt:100,state:'fresh'}})});await p.select('wsl');await p.perform('models',{id:'saved'});const nodes=all(p.root);assert.ok(nodes.some(node=>node.tagName==='li'&&node.textContent==='Reasoning Model · model-id'));assert.ok(nodes.some(node=>node.tagName==='small'&&node.textContent==='推理强度: low / High'));assert.ok(nodes.some(node=>node.tagName==='small'&&node.textContent==='推理强度: 上游未提供'));assert.ok(nodes.some(node=>node.tagName==='small'&&node.textContent.startsWith('更新于：')));});
test('environment heading and concise status keep safety blockers visible',async()=>{const p=panel({read:async entry=>({...snapshot(entry),activeVerified:false,sharedWithDesktop:false,verification:{status:'pending'},blockers:['Finish active tasks first']})});await p.select('wsl');const nodes=all(p.root),texts=nodes.map(node=>node.textContent);assert.equal(nodes.find(node=>node.tagName==='h3').textContent,'WSL · Codex CLI');assert.ok(texts.includes('Ubuntu · user · /home/user/.codex'));assert.equal(texts.filter(text=>text==='尚未验证登录有效性').length,1);assert.equal(texts.includes('WSL 配置已读取；登录有效性以核验结果为准。'),false);assert.ok(texts.includes('Finish active tasks first'));assert.ok(nodes.find(node=>node.textContent==='切换到此接入').disabled);assert.ok(texts.some(text=>text?.includes('核验状态: pending')&&text.includes('此 WSL 配置独立于本机接入。')));});

test('empty desktop profiles expose add form despite switch-only blockers',async()=>{const p=panel({desktop:true,read:async entry=>({...snapshot(entry),accounts:[],blockers:['Custom routing'],capabilities:{save:true,import:true,capture:false,switch:false,models:true}})});await p.select('wsl');const nodes=all(p.root);assert.ok(nodes.find(node=>node.tagName==='details'&&node.children.some(c=>c.textContent==='添加 WSL API 接入')).open);assert.ok(nodes.some(node=>node.textContent==='保存 API 接入'));assert.equal(nodes.some(node=>node.textContent==='保存当前原生接入'),false);assert.ok(nodes.some(node=>node.textContent==='添加或导入只保存接入，不会切换当前账号。'));});

test('exact native recovery capability does not permit switching other saved APIs',async()=>{
  const calls=[];const p=panel({read:async entry=>({...snapshot(entry),blockers:['Ambiguous legacy provider'],capabilities:{switch:false,restore:true},accounts:[{id:'native',kind:'native',name:'Original'},{id:'saved',kind:'api',name:'Saved'}]}),request:async value=>{calls.push({...value});return snapshot('wsl');}});
  await p.select('wsl');const switches=all(p.root).filter(node=>node.tagName==='button'&&node.textContent==='切换到此接入');
  assert.equal(switches.length,1);assert.equal(switches[0].disabled,false);switches[0].onclick();
  await all(p.root).find(node=>node.textContent==='确认切换').onclick();
  assert.equal(calls.length,1);assert.equal(calls[0].id,'native');assert.equal(calls[0].expectedIdentity,'identity-wsl');
});

test('additional native credentials explain restoration without blocking a saved API switch',async()=>{
  const notice='此接入包含附加认证；首次切换时将完整保留，之后可恢复。';
  const p=panel({desktop:true,read:async entry=>({...snapshot(entry),captureNotice:notice,capabilities:{save:true,import:true,capture:false,switch:true,models:true}})});
  await p.select('wsl');const nodes=all(p.root);
  assert.ok(nodes.some(node=>node.textContent===notice));
  assert.equal(nodes.some(node=>node.textContent==='保存当前原生接入'),false);
  assert.equal(nodes.find(node=>node.textContent==='切换到此接入').disabled,false);
  assert.ok(nodes.some(node=>node.textContent==='保存 API 接入'));
});

test('web empty profiles retain add and explicit import actions despite switch blockers',async()=>{const p=panel({read:async entry=>({...snapshot(entry),accounts:[],blockers:['Finish active tasks'],capabilities:{save:true,import:true,capture:false,switch:false}})});await p.select('wsl');const texts=all(p.root).map(node=>node.textContent);assert.ok(texts.includes('保存 API 接入'));assert.ok(texts.includes('从本机导入 API 接入'));assert.ok(texts.includes('添加或导入只保存接入，不会切换当前账号。'));assert.ok(texts.includes('Ubuntu · user · /home/user/.codex'));});

test('web active profiles allow rename while editing and deleting only inactive profiles',async()=>{
  const p=panel({read:async entry=>({...snapshot(entry),activeId:'current',accounts:[{id:'current',kind:'api',name:'Current',active:true},{id:'saved',kind:'api',name:'Saved'}]})});await p.select('wsl');
  const cards=all(p.root).filter(node=>node.tagName==='section'),labels=card=>all(card).filter(node=>node.tagName==='button').map(node=>node.textContent);
  assert.equal(labels(cards[0]).includes('编辑'),false);assert.equal(labels(cards[0]).includes('删除'),false);assert.ok(labels(cards[0]).includes('重命名'));
  assert.ok(labels(cards[1]).includes('编辑'));assert.ok(labels(cards[1]).includes('删除'));
});
test('passwordless capabilities hide all WSL credential management actions',async()=>{
  const p=panel({read:async entry=>({...snapshot(entry),capabilities:{save:false,import:false,capture:false,switch:false,restore:false,models:true}})});await p.select('wsl');
  const nodes=all(p.root),texts=nodes.map(node=>node.textContent);assert.equal(nodes.some(node=>node.tagName==='input'),false);
  for(const label of ['编辑','删除','重命名','保存 API 接入','保存当前原生接入','从本机导入 API 接入','切换到此接入'])assert.equal(texts.includes(label),false);
  assert.ok(texts.includes('查看上游模型'));
});
test('web explicitly imports a selected public source identity without exposing its key',async()=>{
  const calls=[];const p=panel({request:async value=>{calls.push({...value});return value.action==='import-sources'?{sources:[{id:'local-api',name:'Office',sourceIdentity:'public-source-digest'}]}:snapshot('wsl');}});await p.select('wsl');
  assert.equal(calls.length,0);await p.perform('import-sources');
  const select=all(p.root).find(node=>node.tagName==='select'&&node.children.some(option=>option.value==='local-api'));select.value='local-api';
  const form=all(p.root).find(node=>node.tagName==='form'&&node.children.some(child=>child.children?.includes(select)));form.onsubmit({preventDefault(){}});await Promise.resolve();
  assert.equal(calls.at(-1).action,'import-api');assert.equal(calls.at(-1).sourceId,'local-api');assert.equal(calls.at(-1).sourceIdentity,'public-source-digest');assert.equal(calls.at(-1).apiKey,undefined);
});
test('web edits an inactive API without reading back a saved key',async()=>{
  const p=panel();await p.select('wsl');all(p.root).find(node=>node.textContent==='编辑').onclick();
  assert.equal(p.key.type,'password');assert.equal(p.key.value,'');assert.equal(p.key.required,false);assert.equal(p.key.placeholder,'留空保留已有密钥');
});

test('fresh idle-release attempts remain explicit and available despite runtime blockers',async()=>{
  const calls=[];const p=panel({read:async entry=>({...snapshot(entry),blockers:['Native process remains running'],configurationBlockers:[],runtimeBlockers:['Native process remains running'],capabilities:{switch:false,restore:false,attemptSwitch:true,attemptRestore:true},accounts:[{id:'native',kind:'native',name:'Original'},{id:'saved',kind:'api',name:'Saved'}]}),request:async value=>{calls.push({...value});return snapshot('wsl');}});
  await p.select('wsl');const attempts=all(p.root).filter(node=>node.tagName==='button'&&node.textContent==='检查并切换');assert.equal(attempts.length,2);assert.ok(attempts.every(node=>!node.disabled));
  assert.equal(calls.length,0);attempts[1].onclick();assert.equal(calls.length,0);
  const confirmation=all(p.root).find(node=>node.className==='wsl-account-confirm');assert.ok(all(confirmation).some(node=>node.textContent==='将核验所选 WSL 配置目录的任务、审批和未确认发送；全部空闲后正常重启共享服务。原终端连接可能需要重新连接，不会强制结束进程。'));
  await all(confirmation).find(node=>node.tagName==='button'&&node.textContent==='检查并切换').onclick();assert.equal(calls.length,1);assert.equal(calls[0].action,'switch');assert.equal(calls[0].id,'saved');assert.equal(calls[0].expectedIdentity,'identity-wsl');
});
test('restore attempt capability does not enable a different saved API',async()=>{
  const p=panel({read:async entry=>({...snapshot(entry),blockers:['Native process remains running'],capabilities:{switch:false,restore:false,attemptSwitch:false,attemptRestore:true},accounts:[{id:'native',kind:'native',name:'Original'},{id:'saved',kind:'api',name:'Saved'}]})});await p.select('wsl');
  const actions=all(p.root).filter(node=>node.tagName==='button'&&node.textContent==='检查并切换');assert.equal(actions.length,1);assert.equal(actions[0].disabled,false);
});

test('draft continuity callbacks require a verified switch or completed prior transaction result',async()=>{
  const continued=[],payload={...snapshot('wsl'),draftContinuations:[{id:'sid',previousAccountIdentity:'old',accountIdentity:'new'}]};
  const p=panel({onSwitched:value=>continued.push(value),request:async value=>value.action==='switch'?{...payload,continuationPending:true,continuationError:'Refresh continuity'}:payload});await p.select('wsl');assert.equal(continued.length,0);
  await p.perform('models',{id:'saved'});assert.equal(continued.length,0);await p.perform('switch',{id:'saved'});assert.equal(continued.length,0);assert.equal(p.error,'Refresh continuity');
  p.read=async()=>payload;await p.refresh();assert.equal(continued.length,1);
});

test('refresh keeps an unfinished continuation warning visible without migrating drafts',async()=>{
 let migrated=0;const p=panel({read:async entry=>({...snapshot(entry),continuationPending:true,continuationError:'Continuity still unverified'}),onSwitched:()=>migrated++});await p.select('wsl');assert.equal(p.error,'Continuity still unverified');await p.refresh();assert.equal(p.error,'Continuity still unverified');assert.equal(migrated,0);
});

test('Codex restart confirmation is distinct from releasing a Claude gateway executor',async()=>{
  const p=panel({read:async entry=>({...snapshot(entry),restartRequired:true,capabilities:{switch:true}})});await p.select('wsl');all(p.root).find(node=>node.textContent==='切换到此接入').onclick();const box=all(p.root).find(node=>node.className==='wsl-account-confirm');assert.ok(all(box).some(node=>node.textContent?.includes('原终端连接可能需要重新连接，不会强制结束进程。')));assert.equal(all(box).some(node=>node.textContent?.includes('本网关的空闲执行器')),false);assert.ok(all(box).some(node=>node.tagName==='button'&&node.textContent==='检查并切换'));
});

test('Claude attempt confirmation retains its exact gateway-executor wording',async()=>{
 const p=panel({provider:'claude',read:async entry=>({...snapshot(entry),provider:'claude',capabilities:{switch:false,attemptSwitch:true},blockers:['Own idle executor remains']})});await p.select('wsl');all(p.root).find(node=>node.textContent==='检查并切换').onclick();const box=all(p.root).find(node=>node.className==='wsl-account-confirm');assert.ok(all(box).some(node=>node.textContent==='将先释放此网关的空闲执行器；外部 CLI 或未确认任务仍会阻止切换。'));assert.equal(all(box).some(node=>node.textContent?.includes('正常重启共享服务')),false);
});

test('applied Codex configuration exposes explicit service recovery instead of repeating the switch',async()=>{
 const calls=[],p=panel({read:async entry=>({...snapshot(entry),restartPending:true,restartError:'Service recovery pending',capabilities:{switch:true,recoverRestart:true}}),request:async value=>{calls.push({...value});return snapshot('wsl');}});await p.select('wsl');assert.equal(p.error,'Service recovery pending');assert.equal(all(p.root).some(node=>node.textContent==='切换到此接入'),false);
 all(p.root).find(node=>node.textContent==='检查并恢复共享服务').onclick();assert.equal(calls.length,0);await all(p.root).find(node=>node.textContent==='确认恢复共享服务').onclick();assert.equal(calls.length,1);assert.equal(calls[0].action,'recover-restart');assert.equal(calls[0].expectedIdentity,'identity-wsl');assert.equal(calls[0].id,undefined);assert.equal(calls[0].apiKey,undefined);
});

test('Codex restartPending never signals completed continuity before explicit recovery succeeds',async()=>{
 let continued=0;const calls=[],p=panel({onSwitched:()=>continued++,request:async value=>{calls.push({...value});return value.action==='switch'?{...snapshot('wsl'),restartPending:true,restartError:'Applied; recovery pending',capabilities:{recoverRestart:true}}:snapshot('wsl');}});await p.select('wsl');await p.perform('switch',{id:'saved'});assert.equal(continued,0);assert.equal(p.error,'Applied; recovery pending');assert.equal(p.value.restartPending,true);await p.perform('recover-restart');assert.equal(continued,1);assert.deepEqual(calls.map(value=>value.action),['switch','recover-restart']);
});

test('stopped WSL starts only through the explicit selected-environment button',async()=>{
 const calls=[],p=panel({read:async entry=>({...snapshot(entry),available:false,capabilities:{connect:true}}),request:async value=>{calls.push({...value});return snapshot(value.entry);}});
 await p.select('stopped');await p.refresh();assert.equal(calls.length,0);
 await p.perform('switch',{id:'saved'});assert.equal(calls.length,0);
 const connect=all(p.root).find(node=>node.textContent==='启动并连接 WSL');assert.ok(connect);assert.equal(connect.disabled,false);
 await connect.onclick();assert.equal(calls.length,1);assert.deepEqual(calls[0],{provider:'codex',entry:'stopped',action:'connect'});assert.equal(p.value.available,true);
 assert.equal(all(p.root).some(node=>node.textContent==='启动并连接 WSL'),false);
});
