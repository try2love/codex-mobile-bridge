'use strict';
const test=require('node:test');
const assert=require('node:assert/strict');
const {validateCloud}=require('../desktop/features/connections/cloud.cjs');
const {pairingImage}=require('../desktop/features/connections/qr.cjs');

test('cloud IPC exposes only explicit scoped operations',()=>{
  assert.deepEqual(validateCloud(),{action:'status'});
  assert.deepEqual(validateCloud({action:'entitlement'}),{action:'entitlement'});
  for(const action of ['pairing','cancel-pairing','candidates','approve','reject','revoke','verification','verify-code'])assert.throws(()=>validateCloud({action}));
  const binding='11111111-1111-4111-8111-111111111111';
  const opening={action:'access',binding_id:binding,allowed:true,expected_revision:0,safety_code:'12345678 ABCDEF90 12345678 ABCDEF90 12345678 ABCDEF90 12345678 ABCDEF90'};
  assert.deepEqual(validateCloud(opening),opening);
  assert.throws(()=>validateCloud({...opening,safety_code:'invalid'}));
  for(const action of ['approve','reject','revoke'])assert.throws(()=>validateCloud({action,binding_id:binding,safety_code:opening.safety_code}));
  assert.equal(validateCloud({action:'login',origin:'https://relay.example.test',login:'local',password:'test-only'}).origin,'https://relay.example.test');
  for(const value of [{action:'exec'},[],{action:'status',token:'leak'},
    {action:'enabled',enabled:false},{action:'enabled',enabled:true},{action:'enabled',enabled:'true'},{action:'approve',binding_id:'anything'},
    {action:'login',origin:'https://relay.example.test',login:'local',password:'x'.repeat(1025)}])assert.throws(()=>validateCloud(value));
});

test('cloud origin is not silently normalized before credential selection',()=>{
  for(const origin of ['http://relay.example.test','https://relay.example.test/',
    'https://RELAY.example.test','https://relay.example.test:443','https://relay.example.test:08443',
    'https://user@relay.example.test','https://127.1','https://relay.example.test\n'])assert.throws(()=>validateCloud({action:'capabilities',origin}));
});

test('device discovery is read-only and access switches require an explicit boolean and revision',()=>{
  assert.deepEqual(validateCloud({action:'devices'}),{action:'devices'});
  const value={action:'access',binding_id:'11111111-1111-4111-8111-111111111111',allowed:false,expected_revision:7};
  assert.deepEqual(validateCloud(value),value);
  for(const change of [{allowed:'false'},{allowed:undefined},{expected_revision:-1},{expected_revision:'7'},{expected_revision:Number.MAX_SAFE_INTEGER+1},{expected_revision:undefined},{token:'leak'}]){
    assert.throws(()=>validateCloud({...value,...change}));
  }
});

test('email codes require an explicit server and email scope and never accept tokens or passwords',()=>{
  const value={action:'signup-verify',origin:'https://relay.example.test',login:'email@example.test',verification_code:'012345'};
  assert.deepEqual(validateCloud(value),value);
  for(const code of ['', '12345', '1234567', '12345a', ' 12345', '\uff11\uff12\uff13\uff14\uff15\uff16'])assert.throws(()=>validateCloud({...value,verification_code:code}));
  for(const change of [{origin:''},{login:''},{token:'old-link-token'},{password:'not-to-send'}])assert.throws(()=>validateCloud({...value,...change}));
  assert.throws(()=>validateCloud({action:'verify',origin:value.origin,login:value.login,token:'old-link-token'}));
  for(const action of ['verification','verify-code'])assert.throws(()=>validateCloud({...value,action}));
});

test('registration sends no password until a scoped email proof is provided',()=>{
  const scope={origin:'https://relay.example.test',login:'signup@example.test'};
  const send={action:'signup-code',...scope};
  const verify={action:'signup-verify',...scope,verification_code:'012345'};
  assert.deepEqual(validateCloud(send),send);
  assert.deepEqual(validateCloud(verify),verify);
  for(const value of [send,verify])assert.throws(()=>validateCloud({...value,password:'must-not-be-sent'}));
  const complete={action:'register',...scope,password:'synthetic-password',registration_token:'t'.repeat(43)};
  assert.deepEqual(validateCloud(complete),complete);
  for(const token of ['', 't'.repeat(42), 't'.repeat(44), 't'.repeat(42)+'!', 't'.repeat(42)+'\n'])assert.throws(()=>validateCloud({...complete,registration_token:token}));
  const {registration_token,...withoutProof}=complete;
  assert.throws(()=>validateCloud(withoutProof));
  assert.throws(()=>validateCloud({...verify,verification_code:'12345a'}));
});

test('official cloud preset is an exact HTTPS origin with its explicit service port',()=>{
  const fs=require('node:fs'),path=require('node:path');
  const source=fs.readFileSync(path.join(__dirname,'../desktop/features/connections/cloud.js'),'utf8');
  const origin=source.match(/const officialOrigin='([^']+)'/)[1];
  assert.equal(origin,'https://bridge.waken.cloudns.be:8787');
  assert.deepEqual(validateCloud({action:'capabilities',origin}),{action:'capabilities',origin});
  assert.throws(()=>validateCloud({action:'capabilities',origin:'http://bridge.waken.cloudns.be:8787'}));
});

test('entitlement refresh participates in the application update write guard',()=>{
  const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
  const source=fs.readFileSync(path.join(__dirname,'../desktop/main.cjs'),'utf8');
  const expression=source.match(/const writes=([^\n]+);/)[1];
  for(const action of ['entitlement','login','logout','redeem','access','web-access'])assert.equal(vm.runInNewContext(expression,{action:'cloud',payload:{action}}),true,action);
  for(const action of ['status','capabilities','sponsorship','devices'])assert.equal(vm.runInNewContext(expression,{action:'cloud',payload:{action}}),false,action);
});

test('web permission requires the exact account, gateway and explicit versioned boolean',()=>{
  const value={action:'web-access',origin:'https://relay.example.test',account_id:'11111111-1111-4111-8111-111111111111',gateway_id:'22222222-2222-4222-8222-222222222222',allowed:false,expected_revision:1};
  assert.deepEqual(validateCloud(value),value);
  for(const change of [{allowed:'false'},{allowed:undefined},{expected_revision:undefined},{expected_revision:Number.MAX_SAFE_INTEGER},{account_id:'unknown'},{gateway_id:undefined},{origin:value.origin+'/'},{token:'secret'}])assert.throws(()=>validateCloud({...value,...change}));
});

test('direct pairing still returns a local image, never the one-time secret URL',async()=>{
  const result=await pairingImage({url:'https://direct.example.test/#pair=test-only',expires_at:'2026-10-30T00:05:00Z'});
  assert.match(result.image,/^data:image\/png;base64,/);
  assert.equal(result.url,undefined);
  assert.equal(result.expires_at,'2026-10-30T00:05:00Z');
});
