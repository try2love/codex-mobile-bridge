'use strict';
const {test}=require('node:test'),assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs');
const source=fs.readFileSync(require('node:path').join(__dirname,'../relay/web/mode.js'),'utf8');
function fixture(id){
 class Storage {constructor(){this.data=new Map();}getItem(k){return this.data.get(k)??null;}setItem(k,v){this.data.set(k,v);}removeItem(k){this.data.delete(k);}}
 const window={},storage=new Storage(),context={window,Storage,location:{pathname:'/d/'+id+'/',hash:''},document:{addEventListener(){}}};
 vm.runInNewContext(source,context);return {window,storage};
}
test('relay scopes API, attachment and download URLs once, with no cross-device admission',()=>{
 const f=fixture('a'.repeat(32)),prefix='/d/'+'a'.repeat(32);
 assert.equal(f.window.BridgeRelayURL('/api/sessions'),prefix+'/api/sessions');
 assert.equal(f.window.BridgeRelayURL(prefix+'/api/sessions'),prefix+'/api/sessions');
 assert.equal(f.window.BridgeRelayPath(prefix+'/api/sessions'),'/api/sessions');
 assert.equal(f.window.BridgeRelayPath('/d/'+'b'.repeat(32)+'/api/sessions'),'');
 assert.equal(f.window.BridgeRelayURL('https://other.example/api/test'),'https://other.example/api/test');
 f.storage.setItem('draft','hello');assert.equal(f.storage.getItem('draft'),'hello');assert.equal(f.storage.data.get(prefix+':draft'),'hello');
});
test('download recognizer accepts current scoped computer only',()=>{
 const download=require('../web/features/workspace/downloads.js');const prefix='https://relay.example/d/'+'a'.repeat(32)+'/';
 const file='api/sessions/11111111-1111-4111-8111-111111111111/files/'+'a'.repeat(64);
 assert.equal(download.eligible(prefix+file,prefix),true);
 assert.equal(download.eligible('https://relay.example/'+file,prefix),false);
 assert.equal(download.eligible(prefix.replace('/d/a','/d/b')+file,prefix),false);
});
