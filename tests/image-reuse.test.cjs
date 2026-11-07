'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
function fixture(){
 const nodes=[],events={};
 class Node{
  constructor(tag){this.localName=tag;this.children=[];this.dataset={};this.style={};this.attributes={};this.listeners={};this.hidden=false;this.isConnected=true;this.classList={add(){},remove(){},toggle(){}};nodes.push(this);}
  append(...values){this.children.push(...values);}replaceChildren(...values){this.children=values;}
  setAttribute(name,value){this.attributes[name]=value;}removeAttribute(name){delete this.attributes[name];}querySelector(tag){return this.children.find(node=>node.localName===tag);}
  addEventListener(name,handler){this.listeners[name]=handler;}hasPointerCapture(){return false;}
  showModal(){this.open=true;}close(){this.open=false;this.listeners.close?.();}focus(){this.focused=true;}
 }
 const document={body:new Node('body'),createElement:tag=>new Node(tag),addEventListener:(name,handler)=>events[name]=handler},window={};
 vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../web/shared/image-viewer.js'),'utf8'),{document,window,URL,BridgeDownload:require('../web/features/workspace/downloads.js'),location:new URL('https://gateway.test/'),ResizeObserver:class{observe(){}disconnect(){}},requestAnimationFrame(){return 1;},cancelAnimationFrame(){},performance:{now:()=>1}});
 const open=target=>{target.closest=()=>target;events.click({target,button:0,preventDefault(){}});};
 const node=id=>nodes.find(node=>node.id===id),source='/api/sessions/aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee/uploads/aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee/preview?host=local&variant=original';
 return {api:window.BridgeImageViewer,open,node,create:tag=>new Node(tag),source};
}

test('image reuse is explicit and rejects a scope change before adding to a draft',async()=>{
 const ui=fixture(),target=ui.create('img');target.dataset.imagePreview=ui.source;target.dataset.imageName='image.png';
 let scope='gateway/account/session-a',adds=0;
 ui.api.configureComposer({scope:()=>scope,canReuse:value=>value.url?.includes('/uploads/'),add:async()=>{adds++;}});
 ui.open(target);assert.equal(ui.node('image-preview-reuse').hidden,false);assert.equal(adds,0);
 scope='gateway/account/session-b';await ui.node('image-preview-reuse').onclick();assert.equal(adds,0);
 scope='gateway/account/session-a';await ui.node('image-preview-reuse').onclick();assert.equal(adds,1);assert.equal(ui.node('image-preview-dialog').open,false);
});

test('registered workspace images retain their pre-read composer identity and copied attributes grant nothing',()=>{
 const ui=fixture();let scope='old-account';const context={provider:'codex',id:'a',host:'local'};
 ui.api.configureComposer({scope:()=>scope,canReuse:value=>value.url?ui.api.isRegisteredSource(value):value.context?.id==='a',add:async()=>{}});
 const captured=ui.api.composerScope(context);scope='new-account';
 const result={kind:'image',mime:'image/png',data:'aGVsbG8=',name:'image.png',size:5},target=ui.create('img');target.src='data:image/png;base64,'+result.data;
 assert.equal(ui.api.registerWorkspaceImage(target,result,context,captured),true);ui.open(target);assert.equal(ui.node('image-preview-reuse').hidden,true);
 ui.node('image-preview-close').onclick();scope='old-account';ui.open(target);assert.equal(ui.node('image-preview-reuse').hidden,false);
 ui.node('image-preview-close').onclick();const forged=ui.create('img');forged.dataset.imagePreview=target.src;ui.open(forged);assert.equal(ui.node('image-preview-dialog').open,false);
});

test('an old asynchronous add cannot close the preview opened afterwards',async()=>{
 const ui=fixture(),first=ui.create('img'),next=ui.create('img');first.dataset.imagePreview=ui.source;next.dataset.imagePreview=ui.source;next.dataset.imageName='next.png';
 let finish;ui.api.configureComposer({scope:()=> 'same-draft',canReuse:()=>true,add:()=>new Promise(resolve=>finish=resolve)});
 ui.open(first);const pending=ui.node('image-preview-reuse').onclick();ui.open(next);finish();await pending;assert.equal(ui.node('image-preview-dialog').open,true);
});

test('ZCode history previews share exact same-origin and session-bound URL validation',()=>{
 for(const url of ['https://other.test/api/desktop-sessions/zcode/images/'+ 'a'.repeat(64)+'?sessionId=sess-test','/api/desktop-sessions/zcode/images/'+ 'a'.repeat(64)]){
  const ui=fixture(),target=ui.create('img');target.dataset.imagePreview=url;ui.open(target);assert.equal(ui.node('image-preview-dialog'),undefined);
 }
 const ui=fixture(),target=ui.create('img');target.dataset.imagePreview='/api/desktop-sessions/zcode/images/'+ 'a'.repeat(64)+'?sessionId=sess-test';target.dataset.imageName='native.png';ui.open(target);assert.equal(ui.node('image-preview-dialog').open,true);assert.equal(ui.node('image-preview-title').textContent,'native.png');
});
