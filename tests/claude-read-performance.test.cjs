const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const {webcrypto,createHmac}=require('node:crypto');
const source=fs.readFileSync('bridge/clients/claude/connector.js','utf8');
const sleep=ms=>new Promise(resolve=>setTimeout(resolve,ms));
async function until(predicate){for(let i=0;i<50&&!predicate();i++)await sleep(5);assert.ok(predicate(),'operation must complete without advancing another idle polling timer');}

test('finished reads publish immediately instead of waiting another idle polling cycle',async()=>{
  const config={cwd:'/fixture',request:'request.json',response:'response.json',token:'test',generation:'fixture',connectorRevision:2,reconnect:true};
  const pack=value=>{const payload=JSON.stringify({generation:config.generation,...value});return JSON.stringify({payload,signature:createHmac('sha256',config.token).update(payload).digest('hex')});};
  let request=pack({type:'idle',seq:0}),response,complete;
  const idle=[];
  const api={getAll:async()=>[{sessionId:'bridge',cwd:config.cwd,title:'Codex Bridge Connector'}],
    getSession:()=>new Promise(resolve=>complete=resolve),readFileAtCwd:async()=>({contents:request}),
    writeSessionFile:async(sid,path,text)=>{response=JSON.parse(JSON.parse(text).payload);return {hash:'fixture'};}};
  const window={'claude.web':{LocalSessions:api}};
  const timer=(fn,ms)=>{if(ms===500){const entry={fn};idle.push(entry);return entry;}return setTimeout(fn,ms);};
  const cancel=id=>{if(id?.fn)id.cancelled=true;else clearTimeout(id);};
  const tick=()=>{const entry=idle.shift();if(entry&&!entry.cancelled)entry.fn();};
  const running=vm.runInNewContext(source.replace('__BRIDGE_CONFIG__',JSON.stringify(config)),{window,TextEncoder,Uint8Array,crypto:webcrypto,console,setTimeout:timer,clearTimeout:cancel});
  try{
    await until(()=>response?.connected&&idle.length);
    request=pack({type:'request',seq:1,surface:'code',method:'getSession',args:['native'],expires:Date.now()/1000+10});tick();
    await until(()=>complete&&idle.length);
    complete({sessionId:'native',title:'Complete'});
    await until(()=>response.seq===1&&response.done);
    assert.equal(response.result.title,'Complete');
  }finally{window.__claudeMobileBridge?.stop();while(idle.length)tick();await running;}
});
