'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
function fixture(language='zh'){
 const document={};
 function element(tag){
  const listeners={};return {tag,children:[],dataset:{},attributes:{},open:false,_text:'',
   get textContent(){return this._text+this.children.map(child=>child.textContent).join('');},set textContent(value){this._text=value;},
   append(...children){for(const child of children){child.parent=this;this.children.push(child);}},
   setAttribute(key,value){this.attributes[key]=value;},addEventListener(type,handler){listeners[type]=handler;},
   emit(type,event={}){listeners[type]?.(event);},showModal(){this.open=true;},close(){this.open=false;this.emit('close');},
   remove(){this.parent.children=this.parent.children.filter(child=>child!==this);},focus(){document.activeElement=this;}
  };
 }
 document.createElement=element;document.body=element('body');
 const context=vm.createContext({document,localStorage:{getItem:()=>language,setItem(){}}});
 for(const file of ['web/i18n.js','web/client-lifecycle.js'])vm.runInContext(fs.readFileSync(path.join(__dirname,'..',file),'utf8'),context);
 return {document,choose:client=>vm.runInContext('ClientLifecycle',context).chooseDisable(client),dialog:()=>document.body.children[0]};
}
test('a real close dialog presents three distinct choices and defaults focus to cancel',async()=>{
 for(const expected of [false,true,null]){
  const ui=fixture(),result=ui.choose({id:'deepseek',name:'DSH'}),dialog=ui.dialog();
  const buttons=dialog.children.at(-1).children;assert.equal(dialog.tag,'dialog');assert.equal(dialog.open,true);assert.equal(buttons.length,3);
  assert.match(buttons[0].textContent,/仅停用手机接入.*保留电脑 App 和现有任务/);assert.match(buttons[1].textContent,/同时退出电脑 App.*所有任务结束/);assert.equal(buttons[2].textContent,'取消');
  assert.equal(ui.document.activeElement,buttons[2]);assert.equal(dialog.attributes['aria-labelledby'],dialog.children[0].id);
  buttons[expected===null?2:expected?1:0].onclick();assert.equal(await result,expected);assert.equal(ui.document.body.children.length,0);
 }
});
test('Escape and other dialog close events always cancel the desktop exit choice',async()=>{
 for(const escape of [true,false]){
  const ui=fixture(),result=ui.choose({id:'deepseek',name:'DSH'}),dialog=ui.dialog();let prevented=false;
  if(escape)dialog.emit('cancel',{preventDefault(){prevented=true;}});else dialog.close();
  assert.equal(await result,null);assert.equal(prevented,escape);assert.equal(ui.document.body.children.length,0);
 }
});
test('the shared close dialog is fully translated in English and preserves client names',async()=>{
 const ui=fixture('en'),result=ui.choose({id:'claude',name:'Claude Test'}),dialog=ui.dialog();assert.doesNotMatch(dialog.textContent,/[\u4e00-\u9fff]/);assert.match(dialog.textContent,/Claude Test/);assert.match(dialog.textContent,/Disable phone access only/);assert.match(dialog.textContent,/Also quit the desktop app/);dialog.close();await result;
});
test('both UIs load the shared dialog and desktop packages include it',()=>{
 const read=file=>fs.readFileSync(path.join(__dirname,'..',file),'utf8');
 for(const file of ['web/index.html','desktop/index.html'])assert.match(read(file),/<script[^>]+client-lifecycle\.js/);
 assert.ok(JSON.parse(read('package.json')).build.files.includes('web/client-lifecycle.js'));
 assert.match(read('bridge/httpd.py'),/"\/client-lifecycle\.js"/);
});


test('Claude explains its native quit menu while other clients retain the idle requirement',async()=>{
 for(const language of ['zh','en'])for(const id of ['claude','deepseek','codex']){
  const ui=fixture(language),result=ui.choose({id,name:id}),dialog=ui.dialog(),buttons=dialog.children.at(-1).children,note=buttons[1].children[1].textContent;
  assert.equal(buttons.length,3);assert.doesNotMatch(dialog.textContent,/强制退出|Force quit/i);
  if(id==='claude'){
   assert.match(note,language==='en'?/native menu.*may appear briefly/:/原生菜单.*短暂出现/);
   assert.match(note,language==='en'?/task or save confirmation on your computer/:/任务或保存确认.*电脑端处理/);
   assert.doesNotMatch(note,/only after all tasks finish|仅在所有任务结束/i);
  }else assert.equal(note,language==='en'?'Quit only after all tasks finish and no approvals are pending.':'仅在所有任务结束且没有待确认操作时退出。');
  if(language==='en')assert.doesNotMatch(dialog.textContent,/[\u4e00-\u9fff]/);
  dialog.close();assert.equal(await result,null);
 }
});
