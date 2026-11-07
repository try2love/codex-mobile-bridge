'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const root=path.resolve(__dirname,'..'),read=file=>fs.readFileSync(path.join(root,file),'utf8');
function fixture(host,reply='copied'){
  const events={},writes=[],notices=[],timers=[];
  class Element{
    constructor(tag){this.tag=tag;this.children=[];this.dataset={};this.attributes={};this.className='';this.disabled=false;this.textContent='';this.classList={add:name=>{this.className+=' '+name;}};}
    append(...nodes){for(const node of nodes){node.parent=this;this.children.push(node);}}
    replaceChildren(...nodes){this.children=[];this.append(...nodes);}
    setAttribute(key,value){this.attributes[key]=value;}
    addEventListener(){} showModal(){this.open=true;}close(){this.open=false;}
    closest(selector){return selector.split(',').some(value=>this.className.split(/\s+/).includes(value.trim().replace(/^\./,'')))?this:null;}
  }
  const body=new Element('body'),nodes=new Map();
  const get=id=>{if(!nodes.has(id)){const node=new Element('button');node.textContent='复制';const html=read('web/index.html'),match=html.match(new RegExp('id="'+id+'" class="([^"]+)"'));node.className=match?.[1]||'';nodes.set(id,node);}return nodes.get(id);};
  const document={body,createElement:tag=>new Element(tag),addEventListener(name,fn){(events[name]||=[]).push(fn);},removeEventListener(name,fn){events[name]=(events[name]||[]).filter(value=>value!==fn);},dispatchEvent(event){for(const fn of events[event.type]||[])fn(event);}};
  const c={document,navigator:{},prompt:(command,text)=>{writes.push({command,text});return reply;},setTimeout:fn=>timers.push(fn),CustomEvent:class{constructor(type,init){this.type=type;this.detail=init.detail;}},BridgeI18n:{t:x=>x,source:()=>null,language:()=> 'zh'},get,$:get,t:x=>x,toast:x=>notices.push(x)};
  c.window=c;c.top=c;vm.createContext(c);vm.runInContext(read('web/features/chat/message-actions.js'),c);
  document.addEventListener('bridge-message-error',e=>notices.push(e.detail));
  vm.runInContext(read('tests/fixtures/mobile-clipboard-v215.js').replace('__BRIDGE_CLIPBOARD_TOKEN__',host+'-synthetic-token'),c);
  const click=async(button,trusted=true)=>{for(const fn of events.click||[])fn({isTrusted:trusted,target:button});await button.onclick();};
  return {c,get,body,Element,writes,notices,click};
}
for(const host of ['ios','android']){
  test(host+': first click on a rendered session-menu action copies and dismisses before feedback',async()=>{
    for(const [sessionId,link,label,text] of [
      ['codex-id','codex://threads/019d0000-1234-7123-8123-123456789abc','复制深度链接','codex://threads/019d0000-1234-7123-8123-123456789abc'],
      ['claude-native-id',null,'复制 Session ID','claude-native-id'],
      ['dsh-native-id',null,'复制 Session ID','dsh-native-id'],
      ['sess_zcode',null,'复制 Session ID','sess_zcode']
    ]){
      const f=fixture(host);vm.runInContext(read('web/features/chat/session-menu.js'),f.c);
      const Menu=vm.runInContext('SessionMenu',f.c),menu=Object.create(Menu.prototype);
      Object.assign(menu,{context:{title:'fixture',sessionId,link,pin(){}},box:new f.Element('dialog'),t:x=>x,
        position(){},icon:()=>new f.Element('svg'),close(){this.box=null;},
        notify:message=>{assert.equal(menu.box,null,'success toast is no longer obscured by a modal');f.notices.push(message);}});
      menu.render();
      const copy=menu.box.children.find(node=>node.children.some(child=>child.textContent===label));
      assert.ok(copy,'expected provider copy action');
      await f.click(copy,false);assert.equal(f.writes.filter(write=>write.command.startsWith('codexbridge-copy:')).length,0);
      await f.click(copy);
      assert.deepEqual(f.writes.filter(write=>write.command.startsWith('codexbridge-copy:')).map(write=>write.text),[text]);
      assert.deepEqual(f.notices,['已复制']);
    }
  });
  test(host+': old native injection copies title link and acknowledges visibly',async()=>{
    const f=fixture(host),id='019d0000-1234-7123-8123-123456789abc';
    vm.runInContext(`let currentId=${JSON.stringify(id)},currentHost='local';`,f.c);
    const source=read('web/features/chat/session-menu.js');vm.runInContext(source,f.c);const SessionMenu=vm.runInContext('SessionMenu',f.c);const button=f.get('copy-session-link');button.className='message-copy';button.onclick=()=>SessionMenu.prototype.copy.call({valid(){},t:x=>x,notify:x=>f.notices.push(x)},'codex://threads/'+id,button);
    await f.click(f.get('copy-session-link'));
    assert.equal(f.writes.length,2); // Language sync plus one acknowledged copy.
    assert.equal(f.writes.at(-1).text,'codex://threads/'+id);assert.equal(f.get('copy-session-link').textContent,'已复制');assert.deepEqual(f.notices,['已复制']);
  });
  test(host+': file/folder path copies keep async target request and existing native bridge',async()=>{
    for(const kind of ['file','directory']){
      const f=fixture(host);vm.runInContext(read('web/features/workspace/file-actions.js'),f.c);
      const session={},requests=[],workbench={current:session,url:(s,action,p)=>'/'+action+'?path='+p,request:async url=>{requests.push(url);return {absolutePath:'D:\sample_project\file.txt'};},notify:message=>f.notices.push(message)};
      f.c.BridgeFileActions.menu(workbench,session,{name:'file.txt',path:'file.txt',kind});
      const copy=f.body.children[0].children.find(node=>node.tag==='button');await f.click(copy);
      assert.equal(f.writes.at(-1).text,'D:\sample_project\file.txt');assert.deepEqual(requests,['/info?path=file.txt']);assert.deepEqual(f.notices,['已复制']);
    }
  });
  test(host+': DSH title action copies exact native Session ID',async()=>{
    const f=fixture(host),source=read('web/features/clients/desktop-sessions.js');
    const method=source.slice(source.indexOf('  chatDetails(){'),source.indexOf('  async loadNotificationPolicy(){'));
    const invoke=vm.runInContext('({'+method+'}).chatDetails',f.c);
    vm.runInContext(read('web/features/chat/session-menu.js'),f.c);const SessionMenu=vm.runInContext('SessionMenu',f.c);
    const copy=new f.Element('button');copy.className='message-copy';copy.textContent='复制 Session ID';
    const view={provider:'deepseek',sid:'original-dsh-session',state:{title:'Fixture'},rows:[],onOpenSessionMenu(row){copy.onclick=()=>SessionMenu.prototype.copy.call({valid(){},t:x=>x,notify:x=>f.notices.push(x)},f.c.BridgeSessionLinks.text({provider:'deepseek',id:row.id}),copy);}};
    invoke.call(view);await f.click(copy);
    assert.equal(f.writes.at(-1).text,'original-dsh-session');assert.deepEqual(f.notices,['已复制']);
  });
  test(host+': native rejection shows failure and never a success toast',async()=>{
    const f=fixture(host,'denied');vm.runInContext(`let currentId='019d0000-1234-7123-8123-123456789abc',currentHost='local';`,f.c);
    const source=read('web/features/chat/session-menu.js');vm.runInContext(source,f.c);const SessionMenu=vm.runInContext('SessionMenu',f.c);const button=f.get('copy-session-link');button.className='message-copy';button.onclick=()=>SessionMenu.prototype.copy.call({valid(){},t:x=>x,notify:x=>f.notices.push(x)},'codex://threads/test',button);
    await f.click(f.get('copy-session-link'));assert.deepEqual(f.notices,['复制失败，请重试']);assert.equal(f.get('copy-session-link').textContent,'复制');
  });
}

test('old native label from a prior copy cannot report success for an untrusted or failed attempt',async()=>{
  const f=fixture('ios');const button=new f.Element('button');button.className='message-copy';button.textContent='已复制';
  button.onclick=()=>f.c.BridgeClipboard.copyWithFeedback(()=>{throw Error('读取失败');},button,message=>f.notices.push(message));
  await f.click(button,false);assert.deepEqual(f.notices,[]);
  await f.click(button,true);assert.deepEqual(f.notices,['读取失败']);
});
