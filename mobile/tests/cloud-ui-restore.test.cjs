'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const root=path.resolve(__dirname,'../..');
const java='mobile/android/src/io/github/try2love/codexbridge/';
const read=file=>fs.readFileSync(path.join(root,file),'utf8');
const cloud=read(java+'CloudComputers.java'),activity=read(java+'MainActivity.java'),ios=read('mobile/ios/BridgePreview/App.swift');

function startup(raw,configured=true){
  // Execute the selector used by MainActivity at cold startup, isolating preference and UI I/O.
  const calls=[];
  const code=cloud.split('    boolean restore(){')[1].split('    void forgetRoute')[0].replace(/}\s*$/,'')
    .replace('String raw=','let raw=').replace('"home".equals(raw)','raw==="home"').replace('JSONObject route=new JSONObject(raw)','const route=new Record(raw)')
    .replace('new JSONException','new Error').replace('catch(JSONException unavailable)','catch(unavailable)');
  class Record{constructor(value){this.data=JSON.parse(value);}getString(key){if(typeof this.data[key]!=='string')throw Error(key);return this.data[key];}}
  const app={prefs:{getString:()=>raw},home:()=>calls.push('home'),message:()=>calls.push('message')};
  const restored=vm.runInNewContext('(()=>{'+code+'})()',{app,Record,LAST:'last-cloud-view',enabled:()=>configured,
    open:(p,b,n)=>calls.push(['open',p,b,n]),forgetRoute:()=>calls.push('forget')});
  return {restored,calls};
}

test('an explicit cloud view takes precedence over any previously saved direct origin',()=>{
  assert.match(activity,/!openIntent\(getIntent\(\)\)\s*&&\(cloud==null\|\|!cloud\.restore\(\)\)/);
  const record=JSON.stringify({profile:'owner-A',binding:'binding-A',name:'Mac'});
  assert.deepEqual(startup(record),{restored:true,calls:[['open','owner-A','binding-A','Mac']]});
  assert.deepEqual(startup(null),{restored:false,calls:[]},'legacy direct startup remains available');
  assert.deepEqual(startup('home'),{restored:true,calls:['home']});
  for(const result of [startup(record,false),startup('{}')]){
    assert.deepEqual(result,{restored:true,calls:['forget','home','message']});
  }
});

test('iOS cloud resume also selects an owner and binding, never a cached relay URL',()=>{
  const selector=ios.split('private func restoreCloud()->Bool{')[1].split('private func addCloud')[0];
  assert.match(selector,/defaults\.dictionary\(forKey:lastCloudKey\)/);
  assert.match(selector,/openCloud\(profile,binding,name\);return true/);
  assert.match(ios,/if !restoredCloud\{\s*if let active = defaults\.string\(forKey: "active"\)/);
  assert.match(ios,/defaults\.set\(\["profile":profile,"binding":id,"name":resolved\],forKey:self\.lastCloudKey\)/);
  assert.match(cloud,/putString\(LAST,route.toString\(\)\).remove\("active"\)/);
});

test('cloud and direct cards use the shared Manage action row in both native apps',()=>{
  assert.match(activity,/computerActions\(computer,"管理这台电脑"/);
  assert.match(cloud,/app\.computerActions\(card,"管理云服务"/);
  assert.match(activity,/first\.setMarginEnd\(dp\(8\)\)/);
  assert.match(ios,/computerActions\("管理云服务"/);
  assert.match(ios,/computerActions\("管理这台电脑"/);
});
