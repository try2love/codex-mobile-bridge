'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs');
const context=vm.createContext({BridgeI18n:{t:x=>x},window:{}});
vm.runInContext(fs.readFileSync('web/features/workspace/workbench.js','utf8'),context);
const Workbench=vm.runInContext('Workbench',context);
test('host reads preserve selected host and project writes do not expand scope',()=>{
 const wb=Object.create(Workbench.prototype),session={id:'thread',host:'remote:ssh'};
 for(const action of ['','info','preview','download']){
  const url=new URL(wb.url(session,action,'F:/outside/file.txt'),'http://fixture');
  assert.equal(url.searchParams.get('host'),'remote:ssh');assert.equal(url.searchParams.get('scope'),'host');
  assert.equal(url.searchParams.get('path'),'F:/outside/file.txt');
 }
 for(const action of ['upload','git-status','git-action','references'])assert.ok(!wb.url(session,action).includes('scope='));
 assert.ok(!wb.url(session,'info','folder/file','project').includes('scope='));
});
test('host metadata can reference only an original project relative path',()=>{
 const wb=Object.create(Workbench.prototype),session={},references=[],notices=[];
 Object.assign(wb,{current:session,composer:{focus(){}},select(){},notify:t=>notices.push(t),attachReference:info=>{references.push(info);return true;}});
 assert.equal(wb.referenceFile(session,{path:'/outside/item',projectPath:null}),false);
 assert.equal(references.length,0);assert.equal(notices.length,1);
 assert.equal(wb.referenceFile(session,{path:'F:/project/nested/item',absolutePath:'F:/project/nested/item',projectPath:'nested/item'}),true);
 assert.equal(references[0].path,'nested/item');assert.equal(references[0].absolutePath,'F:/project/nested/item');
 assert.equal(wb.referenceFile({},references[0]),false);
});
test('navigation cancels an earlier relative file lookup before it can replace the file tab',async()=>{
 const wb=Object.create(Workbench.prototype),session={id:'thread',host:'local',files:[]};let finish;
 Object.assign(wb,{current:session,request:()=>new Promise(resolve=>finish=resolve)});
 const pending=wb.locate(session,'relative.txt');wb.cancelLocate();
 finish({absolutePath:'/old/relative.txt'});await pending;
 assert.equal(session.files.length,0);assert.equal(wb.locateState,undefined);
});
test('pending back navigation stays on the requested path and stops at native roots',()=>{
 const wb=Object.create(Workbench.prototype);
 for(const [path,parent] of [['/project/src/lib','/project/src'],['/project/','/'],['/',null],
   ['F:\\project\\src','F:/project'],['F:/project','F:/'],['F:/',null],
   ['//server/share/folder','//server/share'],['//server/share/',null],['relative/path',null]]){
  assert.equal(wb.directoryParent(path),parent,path);
 }
});
