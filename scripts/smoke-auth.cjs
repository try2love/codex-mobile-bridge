'use strict';
// Packaged-runtime login and access-control checks. Isolated data, synthetic
// credentials and loopback requests only; no Codex chat is opened or executed.
const assert=require('node:assert/strict'),fs=require('node:fs/promises'),path=require('node:path'),net=require('node:net');
const {runWorker}=require('../desktop/controller.cjs');
const root=path.resolve(__dirname,'..'),delay=ms=>new Promise(resolve=>setTimeout(resolve,ms));
async function main(){
  const executable=process.argv[2]||path.join(root,'dist/desktop',process.platform==='darwin'?
    `${process.arch==='x64'?'mac':'mac-'+process.arch}/Codex Mobile Bridge.app/Contents/Resources/gateway/codex-mobile-gateway`:
    'win-unpacked/resources/gateway/codex-mobile-gateway.exe');
  await fs.mkdir(path.join(root,'.tmp'),{recursive:true});
  const dataDir=await fs.mkdtemp(path.join(root,'.tmp','auth-中文 '));
  const probe=net.createServer();await new Promise(resolve=>probe.listen(0,'127.0.0.1',resolve));
  const port=probe.address().port;await new Promise(resolve=>probe.close(resolve));
  const worker=(action,payload)=>runWorker({executable,dataDir},action,payload),base='http://127.0.0.1:'+port;
  const password='synthetic-auth-smoke-password';
  async function request(route,{body,cookie}={}){
    const response=await fetch(base+route,{method:body?'POST':'GET',headers:{Origin:base,'Content-Type':'application/json','User-Agent':'Synthetic iPhone',...(cookie?{Cookie:cookie}:{})},body:body?JSON.stringify(body):undefined,signal:AbortSignal.timeout(3000)});
    return {status:response.status,cookie:response.headers.get('set-cookie'),body:await response.json()};
  }
  const login=()=>request('/api/login',{body:{username:'admin',password}});
  async function start(){
    await worker('start');const until=Date.now()+25000;
    while(Date.now()<until){if((await worker('snapshot')).runtime.running)return;await delay(300);}
    throw Error('Isolated gateway failed to start');
  }
  try{
    let state=await worker('snapshot');
    Object.assign(state.preferences,{codexHome:dataDir,port,autoStart:false,lan:false,tunnel:false,connections:[]});
    Object.assign(state.auth,{sessionHours:0,password});await worker('save',state);
    await start();let signed=await login();assert.equal(signed.status,200);assert.match(signed.cookie,/Max-Age=34560000/);
    let cookie=signed.cookie.split(';')[0];
    let devices=await worker('devices',{action:'list'});assert.equal(devices.sessions.length,1);
    assert.equal(devices.sessions[0].ip,'127.0.0.1');assert.equal(devices.sessions[0].userAgent,'Synthetic iPhone');
    assert.equal(devices.sessions[0].expires,0);assert.equal(devices.sessions[0].csrf,undefined);
    const raw=await fs.readFile(path.join(dataDir,'auth-sessions.json'),'utf8');assert.equal(raw.includes(cookie.split('=')[1]),false);
    const accounts=await worker('accounts',{action:'addApi',name:'Synthetic API',baseUrl:'https://fixture.invalid/v1',apiKey:'synthetic-account-key',model:'gemini-fixture'});
    assert.equal(accounts.accounts.length,1);assert.equal(JSON.stringify(accounts).includes('synthetic-account-key'),false);
    const accountId=accounts.accounts[0].id;
    await worker('stop');await start();
    assert.equal((await request('/api/auth',{cookie})).body.authenticated,true);
    const savedAccounts=await request('/api/accounts',{cookie});
    assert.equal(savedAccounts.status,200);assert.equal(savedAccounts.body.accounts[0].id,accountId);
    assert.equal(JSON.stringify(savedAccounts.body).includes('synthetic-account-key'),false);
    assert.equal((await request('/api/accounts')).status,401);
    await worker('accounts',{action:'delete',id:accountId});
    assert.equal((await worker('accounts',{action:'list'})).accounts.length,0);
    await worker('devices',{action:'revoke',id:devices.sessions[0].id});
    assert.equal((await request('/api/auth',{cookie})).body.authenticated,false);
    signed=await login();assert.equal(signed.status,200);cookie=signed.cookie.split(';')[0];
    devices=await worker('devices',{action:'list'});
    await worker('devices',{action:'block',id:devices.sessions[0].id});
    assert.equal((await login()).status,403);assert.equal((await request('/api/sessions',{cookie})).status,403);
    assert.equal((await worker('snapshot')).runtime.running,true);
    await worker('stop');await start();assert.equal((await login()).status,403);
    await worker('devices',{action:'save',policy:{allowlistEnabled:true,allowlist:['192.0.2.10']}});
    assert.equal((await login()).status,403);
    await worker('devices',{action:'save',policy:{allowlistEnabled:true,allowlist:['127.0.0.1'],blocklist:['127.0.0.1']}});
    assert.equal((await login()).status,403);
    await worker('devices',{action:'save',policy:{}});
    signed=await login();assert.equal(signed.status,200);cookie=signed.cookie.split(';')[0];
    await worker('stop');state=await worker('snapshot');state.auth.sessionHours=1;await worker('save',state);await start();
    // Lifetime changes apply to new sessions; existing device access is preserved.
    assert.equal((await request('/api/auth',{cookie})).body.authenticated,true);
    signed=await login();assert.equal(signed.status,200);assert.match(signed.cookie,/Max-Age=3600/);
    cookie=signed.cookie.split(';')[0];
    await worker('stop');state=await worker('snapshot');state.auth.password='synthetic-replacement-password';await worker('save',state);await start();
    assert.equal((await request('/api/auth',{cookie})).body.authenticated,false);
    assert.equal((await request('/api/login',{body:{username:'admin',password:'synthetic-replacement-password'}})).status,200);
    console.log('PASS: packaged login lifetime, durable restart, device list, revocation, IP block/relogin denial, allowlist, desktop recovery and password-change invalidation.');
  }finally{
    try{await worker('stop');}catch{}
    await fs.rm(dataDir,{recursive:true,force:true});
  }
}
main().catch(error=>{console.error(error);process.exitCode=1;});
