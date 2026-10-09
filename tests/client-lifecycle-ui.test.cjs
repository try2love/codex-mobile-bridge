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
 return {document,choose:client=>vm.runInContext('ClientLifecycle',context).chooseDisable(client),initialize:client=>vm.runInContext('ClientLifecycle',context).chooseInitialize(client),dialog:()=>document.body.children[0]};
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

test('initialization requires an explicit foreground confirmation and defaults to cancel',async()=>{
 for(const language of ['zh','en'])for(const accepted of [false,true]){
  const ui=fixture(language),result=ui.initialize({id:'claude',name:'Claude'}),dialog=ui.dialog(),buttons=dialog.children.at(-1).children;
  assert.equal(buttons.length,2);assert.equal(ui.document.activeElement,buttons[1]);assert.match(dialog.textContent,language==='en'?/developer tools.*keyboard focus.*Pause your work/:/开发者工具.*键盘焦点.*暂停电脑端操作/);if(language==='en')assert.doesNotMatch(dialog.textContent,/[\u4e00-\u9fff]/);
  buttons[accepted?0:1].onclick();assert.equal(await result,accepted);
 }
 for(const escape of [false,true]){const ui=fixture(),result=ui.initialize({id:'claude'}),dialog=ui.dialog();if(escape)dialog.emit('cancel',{preventDefault(){}});else dialog.close();assert.equal(await result,false);}
});

test('connection labels distinguish startup, required action, failure and actual connectivity',()=>{
 const {connection}=require('../web/client-lifecycle.js'),base={enabled:true,configured:true,installed:true,connected:false};
 for(const running of [false,true])for(const configured of [false,true]){
  assert.equal(connection({...base,running,configured,connectionState:'starting'}).label,'正在启动应用…');assert.equal(connection({...base,running,configured,connectionState:'connecting'}).label,'正在连接，请稍候…');
 }
 for(const [setupStatus,label] of [['needs-initialization','需要初始化连接'],['needs-permission','等待授权'],['failed','连接失败']])assert.equal(connection({...base,running:false,setupStatus}).label,label);
 assert.equal(connection({...base,connected:true,connectionState:'connecting'}).label,'已连接');assert.equal(connection({...base,connected:true,connectionState:'connecting'}).waiting,false);
 assert.equal(connection({...base,connectionState:'timeout',retryable:true}).retryable,true);assert.equal(connection({...base,connectionState:'connecting',retryable:true}).retryable,false);
 assert.equal(connection({...base,setupStatus:'recovery-required',connectionState:'connecting',backgroundRunning:true,mainRunning:false}).label,'后台运行，桌面未打开');
 assert.equal(connection({...base,pendingEnable:true,reason:'请手动连接 Claude'}).reason,'正在等待客户端连接，完成后会自动显示聊天。');
 for(const setupStatus of ['needs-initialization','needs-retry','needs-developer-mode','needs-trust','needs-permission','failed'])assert.equal(connection({...base,id:'claude',setupStatus}).canInitialize,true);
 for(const client of [{...base,id:'deepseek',setupStatus:'needs-initialization'},{...base,id:'claude',connected:true,setupStatus:'needs-initialization'},{...base,id:'claude',connectionState:'connecting',setupStatus:'needs-initialization'},{...base,id:'claude',setupStatus:'unsupported'}])assert.equal(connection(client).canInitialize,false);
 assert.equal(connection({...base,setupStatus:'restart-required',retryable:true}).retryable,false);
});

test('lock and desktop-unavailable states stop waiting without disconnecting an existing client',()=>{
 const {connection,sessionNotice}=require('../web/client-lifecycle.js');
 for(const [setupStatus,label] of [['needs-unlock','电脑已锁定'],['needs-desktop','电脑桌面暂不可用']]){
  const client={id:'claude',enabled:true,installed:true,connected:false,setupStatus,connectionState:'needs-initialization',reason:'Windows 已锁定；请解锁电脑后重试初始化连接'};const state=connection(client);
  assert.equal(state.label,label);assert.equal(state.waiting,false);assert.equal(state.canInitialize,true);assert.equal(connection({...client,connected:true}).label,'已连接');assert.equal(connection({...client,connected:true}).canInitialize,false);
 }
 assert.match(sessionNotice({state:'locked',interactive:false}),/后台服务继续运行/);assert.equal(sessionNotice({state:'unlocked',interactive:true}),'');
});

test('bounded lifecycle requests abort only their wait and ignore a late result',async()=>{
 const lifecycle=require('../web/client-lifecycle.js');let finish,signal;
 const pending=lifecycle.request(value=>{signal=value;return new Promise(resolve=>finish=resolve);},{timeout:5});await assert.rejects(pending,error=>error.uncertain===true&&/尚未确认/.test(error.message));assert.equal(signal.aborted,true);finish('late');await Promise.resolve();await assert.rejects(pending,/尚未确认/);
 for(const status of [400,401,409,423])assert.equal(lifecycle.failure('quit',Object.assign(Error('rejected'),{status})).uncertain,false);
 for(const error of [Error('offline'),new SyntaxError('bad response'),Object.assign(Error('server'),{status:500})])assert.equal(lifecycle.failure('quit',error).uncertain,true);
 assert.equal(lifecycle.failure('quit',Error('本机操作超时，请刷新状态确认结果'),'desktop').uncertain,true);
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
   assert.match(note,language==='en'?/Quit normally through Claude's native menu/:/原生菜单正常退出/);assert.doesNotMatch(note,/may appear briefly|短暂出现/);
   assert.match(note,language==='en'?/task or save confirmation on your computer/:/任务或保存确认.*电脑端处理/);
   assert.match(note,language==='en'?/After quitting completely.*initialization again.*keyboard focus/:/完全退出后需重新初始化连接.*键盘焦点/);
   assert.doesNotMatch(note,/only after all tasks finish|仅在所有任务结束/i);
  }else assert.equal(note,language==='en'?'Quit only after all tasks finish and no approvals are pending.':'仅在所有任务结束且没有待确认操作时退出。');
  if(language==='en')assert.doesNotMatch(dialog.textContent,/[\u4e00-\u9fff]/);
  dialog.close();assert.equal(await result,null);
 }
});
