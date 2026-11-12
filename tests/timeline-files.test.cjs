'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const context=vm.createContext({window:{},BridgeI18n:{t:value=>value}});
vm.runInContext(fs.readFileSync('web/features/chat/timeline.js','utf8'),context);
const relative=vm.runInContext('timelineFilePath',context);
test('explicit host file actions preserve the captured host and legacy image route',()=>{
 const Type=vm.runInContext('ChatTimeline',context),ui=Object.assign(Object.create(Type.prototype),{url:action=>'/api/sessions/chat/'+action+'?host=remote%3Assh'});
 const url=new URL(ui.fileUrl({id:'host-link',scope:'host',path:'F:/Outside/voice.wav'}),'http://fixture');
 assert.equal(url.searchParams.get('host'),'remote:ssh');assert.equal(url.searchParams.get('scope'),'host');assert.equal(url.searchParams.get('path'),'F:/Outside/voice.wav');
 assert.equal(ui.fileUrl({id:'native-image',image:true}),'/api/sessions/chat/files/native-image?host=remote%3Assh');
});
test('summary paths resolve inside original POSIX, WSL and SSH workspaces',()=>{
 for(const cwd of ['/repo','/home/fixture/项目']){
  assert.equal(relative(cwd+'/space name.py',cwd),'space name.py');
  assert.equal(relative('./src/../space name.py',cwd),'space name.py');
 }
 assert.equal(relative('/file.txt','/'),'file.txt');
 assert.equal(relative('src/<img onerror=alert(1)>.py','/repo'),'src/<img onerror=alert(1)>.py');
});
test('Windows drives and UNC paths retain their selected workspace boundary',()=>{
 assert.equal(relative('c:\\Repo\\src\\file.py','C:\\Repo'),'src/file.py');
 assert.equal(relative('src\\file.py','C:\\Repo'),'src/file.py');
 assert.equal(relative('\\\\server\\share\\repo\\file.py','\\\\server\\share\\repo'),'file.py');
 assert.equal(relative('//SERVER/share/repo/file.py','//server/share/repo'),'file.py');
 assert.throws(()=>relative('D:\\Repo\\file.py','C:\\Repo'));
 assert.throws(()=>relative('\\\\other\\share\\repo\\file.py','\\\\server\\share\\repo'));
});
test('missing context, sibling paths, traversal and URL schemes never become workspace targets',()=>{
 for(const path of ['/repo-other/file.py','/other/file.py','../secret','src/../../secret','https://host/file','file:///repo/file','a\0b'])assert.throws(()=>relative(path,'/repo'));
 assert.throws(()=>relative('/repo/file.py',null));
});
test('summary file controls preserve literal text and only dispatch the supplied path',()=>{
 class Node{
  constructor(tag){this.tagName=tag;this.children=[];this.classList={add(){}};}
  append(...children){this.children.push(...children);}
  replaceChildren(...children){this.children=children;}
 }
 context.document={createElement:tag=>new Node(tag)};
 const Type=vm.runInContext('ChatTimeline',context),calls=[];
 const ui=Object.assign(Object.create(Type.prototype),{onFile:path=>calls.push(path)});
 const path='<img src=x onerror=alert(1)>.py';
 const card=ui.renderRow({role:'fileChanges',fileSummary:{count:1,added:1,removed:0,files:[{path,added:1,removed:0}]}},true);
 const control=card.children[1].children[0].children[0];
 assert.equal(control.tagName,'button');assert.equal(control.type,'button');assert.equal(control.textContent,path);assert.equal(control.children.length,0);
 control.onclick();assert.deepEqual(calls,[path]);
});
test('file callback uses captured session host, validates existence, and cancels stale navigation',async()=>{
 const source=fs.readFileSync('web/shell/app.js','utf8');
 const callback=/onFile:(async path=>\{.*?\}),canEdit:/.exec(source)[1];
 const session={id:'original',host:'wsl:fixture'},requests=[],locations=[],errors=[];
 let finish;
 const scope=vm.createContext({id:'original',host:session.host,currentId:'original',currentHost:session.host,state:{cwd:'/repo'},timelineFilePath:relative,t:value=>value,toast:value=>errors.push(value),workbench:{current:session,url:(target,action,path)=>{assert.equal(target,session);return [target.host,action,path];},request:url=>{requests.push(url);return new Promise(resolve=>finish=resolve);},locate:async(target,path)=>locations.push([target,path])}});
 const open=vm.runInContext('('+callback+')',scope);
 const pending=open('/repo/file.py');assert.deepEqual(requests,[[session.host,'info','file.py']]);
 scope.workbench.current={id:'other',host:'local'};finish({});await pending;assert.deepEqual(locations,[]);
 scope.workbench.current=session;scope.workbench.request=async()=>{throw Error('File not found');};
 await open('/repo/deleted.py');assert.deepEqual(errors,['File not found']);assert.deepEqual(locations,[]);
 scope.workbench.request=async()=>({});await open('/repo/file.py');assert.equal(locations[0][0],session);assert.equal(locations[0][1],'file.py');
 scope.currentHost='ssh:other';await open('/repo/file.py');assert.equal(locations.length,1);
});
