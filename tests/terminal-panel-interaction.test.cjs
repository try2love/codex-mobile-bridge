const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');

function fixture(){
  const timers=new Map(),storage=[];let next=0,uid=0;
  const context=vm.createContext({BridgeI18n:{t:s=>s},BridgeHost:{hasNativeLayout:()=>false},BridgeLayout:{prefersTouchInput:()=>true,isCompactViewport:()=>true},navigator:{userAgent:'iPhone Mobile'},
    uuid:()=>String(++uid),document:{hidden:false,body:{},querySelector:()=>null,activeElement:null},window:{getSelection:()=>({toString:()=>''})},
    sessionStorage:{setItem:(...args)=>storage.push(args),getItem:()=>null,removeItem:(...args)=>storage.push(args)},
    setTimeout:(fn,ms)=>{timers.set(++next,{fn,ms});return next;},clearTimeout:id=>timers.delete(id),queueMicrotask});
  vm.runInContext(fs.readFileSync(path.join(__dirname,'../web/features/workspace/terminal-panel.js'),'utf8')+';globalThis.Panel=TerminalPanel;',context);
  const requests=[];
  const node=(tag,cl,text)=>({tag,cl,textContent:text||'',hidden:false,children:[],append(...values){this.children.push(...values);},replaceChildren(){this.children=[];}});
  const input={value:'',selectionStart:0,selectionEnd:0,focus(){this.focused=true;},setSelectionRange(start,end){this.selectionStart=start;this.selectionEnd=end;},setRangeText(value,start,end){this.value=this.value.slice(0,start)+value+this.value.slice(end);this.selectionStart=this.selectionEnd=start+value.length;this.onselect?.();}};
  const panel=Object.create(context.Panel.prototype);
  Object.assign(panel,{id:'pty-id',running:true,disposed:false,operations:[],completionVersion:0,composing:false,inputMode:'draft',input,
    completions:node(),status:node(),retry:node(),send:node(),term:{options:{},focus(){this.focused=true;}},tab:{body:{contains:()=>false,classList:{toggle(){}}}},session:{},
    workbench:{node,button:(label,click)=>Object.assign(node('button','',label),{click}),isVisible:()=>true,request:(url,body)=>{requests.push(body);return Promise.resolve({});}},url:()=>'/terminal?mode=pty'});
  return {panel,requests,timers,storage,context,node};
}
async function tick(){await new Promise(resolve=>setImmediate(resolve));}

test('PTY shell stays beside live status below the screen, including cmd hint',()=>{
  const f=fixture(),p=f.panel;
  const node=(tag,cl,text)=>({tag,cl,textContent:text||'',title:'',hidden:false,children:[],style:{},classList:{add(){}},
    append(...children){this.children.push(...children);},setAttribute(){},addEventListener(){}});
  p.workbench.node=node;p.workbench.button=(label,click)=>Object.assign(node('button','',label),{click});
  p.tab.body={children:[],classList:{add(){},toggle(){}},append(...children){this.children.push(...children);},replaceChildren(...children){this.children=children;}};
  p.workbench.paint=()=>{};p.layout=()=>{};
  f.context.TerminalFont={control:()=>({label:node('span','font')}),size:()=>14,register(){}};
  f.context.Terminal=class {constructor(){this.parser={registerOscHandler(){}};this.options={};}loadAddon(){}open(){}onWriteParsed(){}attachCustomWheelEventHandler(){}onData(){}onResize(){}write(){}reset(){}};
  f.context.FitAddon={FitAddon:class {}};f.context.ResizeObserver=class {observe(){}};
  p.render({shell:'C:\\Windows\\System32\\cmd.exe'});
  const [head,hint,screen,,,,footer]=p.tab.body.children;
  assert.equal(head.children.includes(p.location),false);
  assert.equal(head.children.length,3);
  assert.equal(hint.hidden,false);
  assert.equal(footer.children[0],p.status);
  assert.equal(footer.children[1],p.location);
  assert.equal(p.location.title,'C:\\Windows\\System32\\cmd.exe');
  assert.ok(p.tab.body.children.indexOf(footer)>p.tab.body.children.indexOf(screen));
  p.status.textContent='Temporary connection error';
  assert.equal(footer.children[1],p.location);
  p.apply({cursor:1,running:true,shell:'/very/long/path/to/zsh',cwd:'/project'});
  assert.equal(p.location.title,'/very/long/path/to/zsh');
  assert.match(p.status.textContent,/连续会话/);
});

function cachedTerminal(){
  const f=fixture(),calls=[];
  Object.assign(f.panel,{hadId:true,running:false,cursor:0,screen:{},reopen:f.node(),render(){},apply(result){this.running=result.running;calls.push('apply');},
    startDuplex(){calls.push('duplex');},schedule(){calls.push('schedule');},focusOnSelect(){calls.push('focus');},save(){calls.push('save');}});
  f.panel.term.cols=80;f.panel.term.rows=24;
  f.panel.workbench.paint=()=>{};
  return {f,calls};
}

test('explicit terminal open restores a live cached PTY without creating another',async()=>{
  const {f,calls}=cachedTerminal(),requests=[];
  f.panel.workbench.request=async(url,body)=>{requests.push({url,body});return body?{}:requests.length===1?{mode:'pty'}:{running:true,cursor:3,output:'saved'};};
  await f.panel.load();
  assert.equal(f.panel.id,'pty-id');assert.equal(f.panel.running,true);
  assert.equal(requests.length,2);assert.deepEqual(calls,['apply','duplex','schedule','focus']);
});

test('explicit open replaces only a typed missing cached PTY',async()=>{
  const {f,calls}=cachedTerminal(),requests=[];
  f.panel.workbench.request=async(url,body)=>{
    requests.push({url,body});
    if(requests.length===1)return {mode:'pty'};
    if(requests.length===2)throw Object.assign(Error('PTY gone'),{status:404,code:'terminal_missing'});
    return {running:true,cursor:0};
  };
  await f.panel.load();
  assert.notEqual(f.panel.id,'pty-id');assert.equal(f.panel.hadId,false);
  assert.equal(requests.length,3);assert.equal(requests[2].body.action,'open');
  assert.equal(requests[2].body.id,f.panel.id);assert.ok(calls.includes('save'));
  assert.equal(f.panel.running,true);
});

test('cached PTY auth, missing thread and network failures never create a replacement',async()=>{
  for(const error of [
    Object.assign(Error('unauthorized'),{status:401}),
    Object.assign(Error('forbidden'),{status:403}),
    Object.assign(Error('thread gone'),{status:404}),
    Error('offline')]){
    const {f,calls}=cachedTerminal(),requests=[];
    f.panel.workbench.request=async(url,body)=>{requests.push({url,body});if(requests.length===1)return {mode:'pty'};throw error;};
    await f.panel.load();
    assert.equal(requests.length,2);assert.equal(f.panel.id,'pty-id');
    assert.equal(calls.includes('save'),false);
    if(!error.status){assert.equal(f.panel.running,true);assert.equal(calls.includes('schedule'),true);}
    else assert.equal(f.panel.running,false);
  }
});

test('cached PTY resumes the same id after a temporary read failure',async()=>{
  const {f,calls}=cachedTerminal(),requests=[];
  f.context.AbortController=AbortController;
  let offline=true;
  f.panel.workbench.request=async(url,body)=>{
    requests.push({url,body});
    if(requests.length===1)return {mode:'pty'};
    if(offline)throw Error('offline');
    return {running:true,cursor:4,output:'continued'};
  };
  await f.panel.load();assert.equal(f.panel.running,true);
  offline=false;await f.panel.poll();
  assert.equal(f.panel.id,'pty-id');assert.equal(f.panel.running,true);
  assert.equal(requests.length,3);assert.equal(requests.some(request=>request.body?.action==='open'),false);
  assert.ok(calls.includes('duplex'));
});

test('short keyboard burst batches in order and retains inputId after uncertain response',async()=>{
  const f=fixture();let calls=0;
  f.panel.workbench.request=async(url,body)=>{f.requests.push({...body});if(++calls===1)throw Error('lost acknowledgement');return {};};
  f.panel.enqueue('a');f.panel.enqueue('😀');f.panel.enqueue('b');
  assert.equal(f.panel.operations.length,1);assert.equal(f.panel.operations[0].data,'a😀b');
  await f.panel.flush();assert.equal(f.panel.inputError,true);
  const id=f.panel.operations[0].id;f.panel.inputError=false;await f.panel.flush();
  assert.deepEqual(f.requests.map(r=>r.inputId),[id,id]);assert.equal(f.panel.operations.length,0);
});

test('continuous typing flushes from the first key rather than postponing the batch',async()=>{
  const f=fixture();f.panel.enqueue('a');const timer=f.panel.flushTimer;
  for(let i=0;i<20;i++)f.panel.enqueue('b');
  assert.equal(f.panel.flushTimer,timer);assert.equal(f.timers.size,1);
  const scheduled=f.timers.get(timer);assert.equal(scheduled.ms,12);scheduled.fn();await tick();
  assert.equal(f.requests[0].data,'a'+'b'.repeat(20));
});

test('input waits for the latest pending resize before reaching the PTY',async()=>{
  const f=fixture();let finishResize;
  f.panel.workbench.request=async(url,body)=>{
    f.requests.push({...body});
    if(body.action==='resize')return new Promise(resolve=>finishResize=resolve);
    return {};
  };
  f.panel.queueResize(90,24);f.panel.queueResize(120,32);f.panel.enqueue('DIRECT_ok');
  const pending=f.panel.flush();await tick();
  assert.deepEqual(f.requests.map(item=>item.action),['resize']);
  assert.deepEqual([f.requests[0].cols,f.requests[0].rows],[120,32]);
  finishResize({});await pending;
  assert.deepEqual(f.requests.map(item=>item.action),['resize','input']);
  assert.equal(f.requests[1].data,'DIRECT_ok');
});

test('flush checks the current layout before sending its first input',async()=>{
  const f=fixture();f.panel.layout=()=>f.panel.queueResize(132,39);
  f.panel.workbench.request=async(url,body)=>{f.requests.push({...body});return {};};
  f.panel.enqueue('DIRECT_ok');await f.panel.flush();
  assert.deepEqual(f.requests.map(item=>item.action),['resize','input']);
  assert.deepEqual([f.requests[0].cols,f.requests[0].rows],[132,39]);
});

test('large 6px terminal fit stays within PTY limits before input',async()=>{
  const f=fixture();f.panel.screen={clientWidth:2560,clientHeight:1200};f.panel.tab.body.classList={toggle(){}};
  f.panel.fit={proposeDimensions:()=>({cols:910,rows:380})};
  Object.assign(f.panel.term,{cols:80,rows:24,resize(cols,rows){this.cols=cols;this.rows=rows;f.panel.queueResize(cols,rows);}});
  f.panel.workbench.request=async(url,body)=>{f.requests.push({...body});return {};};
  f.panel.enqueue('x');await f.panel.flush();
  assert.deepEqual(f.requests.map(item=>item.action),['resize','input']);
  assert.deepEqual([f.requests[0].cols,f.requests[0].rows],[500,300]);
  assert.equal(f.panel.term.cols,500);assert.equal(f.panel.term.rows,300);
  f.panel.fit.proposeDimensions=()=>({cols:1,rows:0});f.panel.layout();
  assert.equal(f.panel.term.cols,2);assert.equal(f.panel.term.rows,2);
});

test('a closed or restarted tab cannot send after waiting for resize',async()=>{
  for(const change of ['disposed','restarting','new-id']){
    const f=fixture();let finishResize;
    f.panel.workbench.request=async(url,body)=>{f.requests.push({...body});if(body.action==='resize')return new Promise(resolve=>finishResize=resolve);return {};};
    f.panel.queueResize(90,25);f.panel.enqueue('old input');const pending=f.panel.flush();await tick();
    if(change==='new-id')f.panel.id='replacement';else f.panel[change]=true;
    finishResize({});await pending;
    assert.deepEqual(f.requests.map(item=>item.action),['resize'],change);
  }
});

test('failed resize keeps input unsent and retry uses the same pending input',async()=>{
  const f=fixture();let fail=true;
  f.panel.workbench.request=async(url,body)=>{f.requests.push({...body});if(body.action==='resize'&&fail){fail=false;throw Error('resize failed');}return {};};
  f.panel.queueResize(100,30);f.panel.enqueue('x');const id=f.panel.operations[0].id;
  await f.panel.flush();assert.equal(f.panel.inputError,true);assert.deepEqual(f.requests.map(item=>item.action),['resize']);
  assert.equal(f.panel.operations[0].id,id);f.panel.inputError=false;await f.panel.flush();
  assert.deepEqual(f.requests.map(item=>item.action),['resize','resize','input']);assert.equal(f.requests[2].inputId,id);
});

test('single completion changes only the mobile draft and never sends input',async()=>{
  const f=fixture();f.panel.input.value='echo "😀/fo"';f.panel.input.selectionStart=f.panel.input.selectionEnd=12;
  f.panel.input.onselect=()=>f.panel.selectionChanged();
  f.panel.workbench.request=async(url,body)=>{f.requests.push(body);return {replaceStart:9,replaceEnd:11,candidates:[{label:'foo',insertText:'foo',isDirectory:false}]};};
  await f.panel.complete();
  assert.equal(f.panel.input.value,'echo "😀/foo"');assert.equal(f.requests[0].action,'complete');assert.equal(f.requests[0].selectionStart,12);
  assert.equal(f.panel.operations.length,0);
});

test('first Tab expands a shared path prefix and second Tab shows choices',async()=>{
  const f=fixture();f.panel.input.value='cd al';f.panel.input.selectionStart=f.panel.input.selectionEnd=5;
  f.panel.workbench.request=async(url,body)=>{
    f.requests.push(body);
    return {replaceStart:3,replaceEnd:body.text.length,candidates:[{label:'alpha/',insertText:'alpha/'},{label:'alpine/',insertText:'alpine/'}],
      commonPrefixInsertText:body.text==='cd al'?'alp':null,truncated:false};
  };
  await f.panel.complete();assert.equal(f.panel.input.value,'cd alp');assert.equal(f.panel.completions.hidden,true);
  await f.panel.complete();assert.equal(f.panel.input.value,'cd alp');assert.equal(f.panel.completions.hidden,false);
  assert.equal(f.requests.length,2);f.panel.completions.children[1].click();
  assert.equal(f.panel.input.value,'cd alpha/');assert.equal(f.panel.operations.length,0);
});

test('empty mobile and local drafts send native Tab without a completion lookup',async()=>{
  for(const mobile of [true,false]){
    const f=fixture();if(!mobile)f.context.navigator.userAgent='Windows NT';
    await f.panel.complete();
    assert.equal(f.panel.operations[0].data,'\t');assert.equal(f.requests.length,0);
  }
});

test('truncated candidates cannot expand a purported common prefix',async()=>{
  const f=fixture();f.panel.input.value='cd al';f.panel.input.selectionStart=f.panel.input.selectionEnd=5;
  f.panel.workbench.request=async()=>({replaceStart:3,replaceEnd:5,candidates:[{label:'alpha/',insertText:'alpha/'},{label:'alpine/',insertText:'alpine/'}],commonPrefixInsertText:'alp',truncated:true});
  await f.panel.complete();assert.equal(f.panel.input.value,'cd al');assert.equal(f.panel.completions.hidden,false);
});

test('a truncated single candidate is offered without changing the draft',async()=>{
  const f=fixture();f.panel.input.value='cd al';f.panel.input.selectionStart=f.panel.input.selectionEnd=5;
  f.panel.workbench.request=async()=>({replaceStart:3,replaceEnd:5,candidates:[{label:'alpha/',insertText:'alpha/'}],truncated:true});
  await f.panel.complete();assert.equal(f.panel.input.value,'cd al');assert.equal(f.panel.completions.hidden,false);
  f.panel.completions.children[1].click();assert.equal(f.panel.input.value,'cd alpha/');
});

test('multiple, empty and stale completions preserve draft until a current choice',async()=>{
  const f=fixture();f.panel.input.value='cd ab';f.panel.input.selectionStart=f.panel.input.selectionEnd=5;
  f.panel.workbench.request=async()=>({replaceStart:3,replaceEnd:5,candidates:[{label:'abc/',insertText:'abc/'},{label:'abd/',insertText:'abd/'}],truncated:true});
  await f.panel.complete();assert.equal(f.panel.input.value,'cd ab');assert.equal(f.panel.completions.hidden,false);
  f.panel.completions.children[1].click();assert.equal(f.panel.input.value,'cd abc/');assert.equal(f.panel.operations.length,0);
  f.panel.workbench.request=async()=>({replaceStart:3,replaceEnd:7,candidates:[]});await f.panel.complete();
  assert.equal(f.panel.input.value,'cd abc/');assert.equal(f.panel.completions.children[0].textContent,'没有匹配的路径');
  let finish;f.panel.workbench.request=()=>new Promise(resolve=>finish=resolve);
  const pending=f.panel.complete();f.panel.input.value='edited';f.panel.clearCompletion();finish({replaceStart:0,replaceEnd:2,candidates:[{insertText:'bad'}]});await pending;
  assert.equal(f.panel.input.value,'edited');
});

test('late completion failures from another tab or edited draft do not overwrite status',async()=>{
  const f=fixture();f.panel.input.value='cd x';f.panel.input.selectionStart=f.panel.input.selectionEnd=4;
  let reject,visible=true;f.panel.workbench.isVisible=()=>visible;
  f.panel.workbench.request=()=>new Promise((resolve,no)=>reject=no);
  let pending=f.panel.complete();visible=false;reject(Error('stale tab'));await pending;assert.equal(f.panel.status.textContent,'');
  visible=true;pending=f.panel.complete();f.panel.input.value='cd y';reject(Error('stale draft'));await pending;assert.equal(f.panel.status.textContent,'');
});

test('pending completion shows local feedback and duplicate Tab shares one request',async()=>{
  const f=fixture();f.panel.input.value='cd src';f.panel.input.selectionStart=f.panel.input.selectionEnd=6;
  let finish,calls=0;f.panel.workbench.request=()=>{calls++;return new Promise(resolve=>finish=resolve);};
  const pending=f.panel.complete();await f.panel.complete();
  assert.equal(calls,1);assert.equal(f.panel.completions.children[0].textContent,'正在补全…');
  finish({replaceStart:3,replaceEnd:6,candidates:[{label:'src/',insertText:'src/'}]});await pending;
  assert.equal(f.panel.input.value,'cd src/');assert.equal(f.panel.completions.hidden,true);
});

test('delayed unchanged select events preserve pending result and visible candidates',async()=>{
  const f=fixture();f.panel.input.value='cd tar';f.panel.input.selectionStart=f.panel.input.selectionEnd=6;
  let finish;f.panel.workbench.request=()=>new Promise(resolve=>finish=resolve);
  const pending=f.panel.complete(),version=f.panel.completionVersion;
  f.panel.selectionChanged();assert.equal(f.panel.completionVersion,version);
  finish({replaceStart:3,replaceEnd:6,candidates:[{label:'target/',insertText:'target/'},{label:'tasks/',insertText:'tasks/'}]});await pending;
  assert.equal(f.panel.completions.hidden,false);
  f.panel.selectionChanged();assert.equal(f.panel.completions.hidden,false);
  f.panel.input.selectionStart=f.panel.input.selectionEnd=5;f.panel.selectionChanged();
  assert.equal(f.panel.completions.hidden,true);
});

test('mobile landscape and iPad keep composer while narrow touch laptop stays direct',()=>{
  const f=fixture();f.context.BridgeLayout.isCompactViewport=()=>false;assert.equal(f.panel.mobile(),true);
  f.context.navigator={userAgent:'Macintosh',platform:'MacIntel',maxTouchPoints:5};assert.equal(f.panel.mobile(),true);
  f.context.navigator={userAgent:'Windows NT',platform:'Win32',maxTouchPoints:10};assert.equal(f.panel.mobile(),false);
});

test('touch drag delegates only tracked mouse scroll to xterm wheel handling',()=>{
  const f=fixture(),wheels=[];
  f.context.WheelEvent=class WheelEvent {static DOM_DELTA_PIXEL=0;constructor(type,options){Object.assign(this,{type},options);}};
  f.panel.term.modes={mouseTrackingMode:'none'};
  f.panel.term.element={style:{},dispatchEvent:event=>wheels.push(event)};
  f.panel.term.hasSelection=()=>false;
  const touch=(id,y)=>({identifier:id,pageY:y,clientX:12,clientY:y});
  const event=(touches,changedTouches=touches)=>({touches,changedTouches,preventDefault(){this.prevented=true;},stopPropagation(){this.stopped=true;}});
  f.panel.touchStart(event([touch(1,100)]));
  const plain=event([touch(1,70)]);f.panel.touchMove(plain);
  assert.equal(wheels.length,0,'normal scrollback stays with native xterm touch handling');
  assert.equal(plain.prevented,undefined);
  f.panel.term.modes.mouseTrackingMode='vt200';f.panel.syncTouchMode();
  assert.equal(f.panel.term.element.style.touchAction,'none');
  f.panel.touchStart(event([touch(1,100)]));
  const tracked=event([touch(1,70)]);f.panel.touchMove(tracked);
  assert.equal(wheels.length,1);assert.equal(wheels[0].deltaY,30);
  assert.equal(wheels[0].deltaMode,0);assert.equal(wheels[0].clientX,12);
  assert.equal(tracked.prevented,true);assert.equal(tracked.stopped,true);
  assert.equal(f.panel.operations.length,0,'drag does not directly enqueue arrow keys');
  f.panel.term.modes.mouseTrackingMode='none';f.panel.syncTouchMode();
  assert.equal(f.panel.term.element.style.touchAction,'');
  const untracked=event([touch(1,40)]);f.panel.touchMove(untracked);
  assert.equal(wheels.length,1,'alternate screen without mouse tracking cannot synthesize arrow keys');
});

test('touch bridge leaves tap, selection, multi-touch and ended gestures alone',()=>{
  const f=fixture(),wheels=[];
  f.context.WheelEvent=class WheelEvent {static DOM_DELTA_PIXEL=0;constructor(type,options){Object.assign(this,{type},options);}};
  f.panel.term.modes={mouseTrackingMode:'drag'};
  f.panel.term.element={style:{},dispatchEvent:event=>wheels.push(event)};
  f.panel.term.hasSelection=()=>true;
  const touch=(id,y)=>({identifier:id,pageY:y,clientX:12,clientY:y});
  const event=(touches,changedTouches=touches)=>({touches,changedTouches,preventDefault(){this.prevented=true;},stopPropagation(){this.stopped=true;}});
  const start=event([touch(1,100)]);f.panel.touchStart(start);
  assert.equal(start.prevented,undefined);
  f.panel.touchMove(event([touch(1,80)]));assert.equal(wheels.length,0);
  f.panel.term.hasSelection=()=>false;
  f.panel.touchMove(event([touch(1,80)]));assert.equal(wheels.length,0,'stationary touch does not scroll');
  f.panel.touchMove(event([touch(1,80),touch(2,80)]));assert.equal(f.panel.touchPoint,null);
  f.panel.touchMove(event([touch(1,60)]));assert.equal(wheels.length,0);
  f.panel.touchStart(event([touch(3,100)]));f.panel.touchEnd(event([], [touch(3,100)]));
  f.panel.touchMove(event([touch(3,50)]));assert.equal(wheels.length,0);
});

test('draft mode forwards tracked wheel data without enabling keyboard or untracked arrow data',async()=>{
  const f=fixture(),panel=f.panel;
  panel.inputMode='draft';panel.term.options.disableStdin=true;
  panel.term.modes={mouseTrackingMode:'vt200'};
  panel.terminalData('keyboard');assert.equal(panel.operations.length,0);
  const wheel={};panel.beginWheelInput(wheel);
  assert.equal(panel.term.options.disableStdin,false);
  panel.terminalData('\x1b[<64;2;3M');
  await tick();
  assert.equal(panel.term.options.disableStdin,true);
  assert.equal(panel.operations[0].data,'\x1b[<64;2;3M');
  panel.terminalData('keyboard');assert.equal(panel.operations[0].data,'\x1b[<64;2;3M');
  panel.term.modes.mouseTrackingMode='none';
  panel.beginWheelInput({});
  assert.equal(panel.term.options.disableStdin,true);
  panel.terminalData('\x1b[A');
  assert.equal(panel.operations[0].data,'\x1b[<64;2;3M');
  await tick();assert.equal(panel.term.options.disableStdin,true);
});

test('desktop focus is purposeful and never steals selection, dialog, editor or IME',()=>{
  const f=fixture();f.context.navigator.userAgent='Desktop';
  assert.equal(f.panel.mobile(),false,'narrow touch-capable desktop uses local input');
  f.panel.focusOnSelect();assert.equal(f.panel.input.focused,true);
  f.panel.inputMode='direct';f.panel.input.focused=false;
  f.panel.focusOnSelect();assert.equal(f.panel.term.focused,true);
  f.panel.term.focused=false;f.panel.composing=true;f.panel.focusOnSelect();assert.equal(f.panel.term.focused,false);
  f.panel.composing=false;f.context.document.activeElement={matches:()=>true};f.panel.focusOnSelect();assert.equal(f.panel.term.focused,false);
  f.context.document.activeElement=null;f.context.window.getSelection=()=>({toString:()=> 'selected'});f.panel.focusOnSelect();assert.equal(f.panel.term.focused,false);
  f.context.window.getSelection=()=>({toString:()=>''});f.context.document.querySelector=()=>({});f.panel.focusOnSelect();assert.equal(f.panel.term.focused,false);
});

test('desktop shortcut and clear screen restore input focus without stealing an editor',()=>{
  const f=fixture();f.context.navigator.userAgent='Windows NT';f.panel.term.clear=()=>{};f.panel.inputMode='direct';
  f.panel.shortcut('Tab','\t');assert.equal(f.panel.term.focused,true);assert.equal(f.panel.operations[0].data,'\t');
  f.panel.term.focused=false;f.panel.clearScreen();assert.equal(f.panel.term.focused,true);
  f.panel.term.focused=false;f.context.document.activeElement={matches:()=>true};f.panel.shortcut('Esc','\x1b');assert.equal(f.panel.term.focused,false);
});

test('desktop local draft responds immediately and Enter sends once without remote key echo',()=>{
  const f=fixture();f.context.navigator.userAgent='Windows NT';
  f.panel.term.paste=text=>f.panel.enqueue(text);f.panel.layout=()=>{};
  f.panel.input.value='echo local';
  let prevented=0;const enter={key:'Enter',preventDefault(){prevented++;}};
  f.panel.handleInputKeydown({key:'Enter',isComposing:true,preventDefault(){throw Error('IME commit blocked');}});
  assert.equal(f.panel.input.value,'echo local');assert.equal(f.panel.operations.length,0);
  f.panel.handleInputKeydown({...enter,repeat:true});assert.equal(f.panel.operations.length,0);
  f.panel.handleInputKeydown(enter);
  assert.equal(prevented,2);assert.equal(f.panel.input.value,'');
  assert.equal(f.panel.operations.length,1);assert.equal(f.panel.operations[0].data,'echo local\r');
  f.panel.handleInputKeydown(enter);
  assert.equal(f.panel.operations[0].data,'echo local\r\r','a fresh Enter explicitly sends an empty line');
  f.panel.handleInputKeydown({key:'Enter',shiftKey:true,preventDefault(){throw Error('Shift+Enter should insert newline');}});
});

test('local draft shortcut arrows edit its caret; direct mode restores remote keys',()=>{
  const f=fixture();f.context.navigator.userAgent='Windows NT';f.panel.layout=()=>{};
  f.panel.input.value='a😀b\ncd';f.panel.input.selectionStart=f.panel.input.selectionEnd=3;
  f.panel.shortcut('←','\x1b[D');assert.equal(f.panel.input.selectionStart,1,'emoji remains intact');
  f.panel.shortcut('→','\x1b[C');assert.equal(f.panel.input.selectionStart,3);
  f.panel.shortcut('↓','\x1b[B');assert.equal(f.panel.input.selectionStart,7);
  f.panel.shortcut('↑','\x1b[A');assert.equal(f.panel.input.selectionStart,3);
  assert.equal(f.panel.operations.length,0);
  f.panel.shortcut('Ctrl+C','\x03');assert.equal(f.panel.operations[0].data,'\x03');
  f.panel.toggleInputMode();assert.equal(f.panel.inputMode,'direct');
  f.panel.shortcut('↑','\x1b[A');assert.equal(f.panel.operations[0].data,'\x03\x1b[A');
});

test('unsent desktop draft stays local and is never written to session storage',()=>{
  const f=fixture();f.context.navigator.userAgent='Windows NT';f.panel.layout=()=>{};
  f.panel.input.value='private command';
  f.panel.toggleInputMode();f.panel.toggleInputMode();
  assert.equal(f.panel.input.value,'private command');
  assert.equal(f.panel.operations.length,0);
  assert.deepEqual(f.storage,[]);
});

test('mobile direct shortcut buttons send remote Esc and arrows',()=>{
  const f=fixture();f.panel.inputMode='direct';f.panel.input.value='unsent draft';
  f.panel.shortcut('Esc','\x1b');f.panel.shortcut('↑','\x1b[A');
  assert.equal(f.panel.input.value,'unsent draft');
  assert.equal(f.panel.operations[0].data,'\x1b\x1b[A');
});


test('new desktop and phone terminals start direct and both can switch modes',()=>{
  const f=fixture();f.context.navigator.userAgent='Desktop';
  f.context.document.addEventListener=()=>{};
  f.context.Panel.prototype.load=()=>{};
  const panel=new f.context.Panel({paint(){}},{key:'direct-default'},{});
  assert.equal(panel.inputMode,'direct');
  Object.assign(panel,{tab:f.panel.tab,input:f.panel.input,term:f.panel.term,running:false});
  panel.layout=()=>{};panel.toggleInputMode();assert.equal(panel.inputMode,'draft');
  panel.toggleInputMode();assert.equal(panel.inputMode,'direct');
  f.context.navigator.userAgent='iPhone Mobile';assert.equal(panel.mobile(),true);
  panel.toggleInputMode();assert.equal(panel.inputMode,'draft');
  panel.toggleInputMode();assert.equal(panel.inputMode,'direct');
});

test('phone direct enables xterm input; compose disables it and makes its hidden textarea read-only',()=>{
  const f=fixture();f.panel.screen={clientHeight:300,clientWidth:400};
  f.panel.fit={proposeDimensions:()=>({cols:80,rows:24})};
  f.panel.term.textarea={readOnly:false,tabIndex:0};
  f.panel.term.cols=80;f.panel.term.rows=24;
  f.panel.modeButton=f.node('button');
  f.panel.inputMode='direct';f.panel.layout();
  assert.equal(f.panel.term.options.disableStdin,false);
  assert.equal(f.panel.term.textarea.readOnly,false);
  assert.equal(f.panel.modeButton.hidden,false);
  assert.equal(f.panel.modeButton.textContent,'终端直接输入模式');
  assert.equal(f.panel.term.focused,undefined,'layout does not open the mobile keyboard');
  f.panel.inputMode='draft';f.context.document.activeElement=f.panel.term.textarea;
  f.panel.term.blur=()=>{f.panel.term.blurred=true;};f.panel.layout();
  assert.equal(f.panel.term.options.disableStdin,true);
  assert.equal(f.panel.term.textarea.readOnly,true);
  assert.equal(f.panel.term.textarea.tabIndex,-1);
  assert.equal(f.panel.term.blurred,true);
  assert.equal(f.panel.modeButton.textContent,'编写后发送模式');
  f.panel.inputMode='direct';f.panel.layout();
  assert.equal(f.panel.term.options.disableStdin,false);
  assert.equal(f.panel.term.textarea.readOnly,false);
});

test('phone direct Tab bypasses stale draft completion and compose Tab uses it',()=>{
  const f=fixture();f.panel.input.value='old draft';f.panel.input.selectionStart=f.panel.input.selectionEnd=9;
  f.panel.inputMode='direct';f.panel.shortcut('Tab','\t');
  assert.equal(f.panel.operations[0].data,'\t');assert.equal(f.requests.length,0);
  f.panel.inputMode='draft';f.panel.input.value='';f.panel.shortcut('Tab','\t');
  assert.equal(f.panel.operations[0].data,'\t\t');assert.equal(f.requests.length,0);
});

test('Enter shortcut sends only one CR in every mode and preserves the unsent draft',()=>{
  for(const [mobile,mode] of [[true,'draft'],[false,'draft'],[false,'direct']]){
    const f=fixture();f.context.navigator.userAgent=mobile?'iPhone Mobile':'Desktop';f.panel.inputMode=mode;
    f.panel.input.value='unsent 中文';f.panel.input.selectionStart=f.panel.input.selectionEnd=2;
    f.panel.enqueue('/model\r');f.panel.shortcut('Enter','\r');
    assert.equal(f.panel.operations[0].data,'/model\r\r');
    assert.equal(f.panel.input.value,'unsent 中文');assert.equal(f.panel.input.selectionStart,2);
  }
});

test('Enter shortcut cannot confirm during composition, closed session or unknown write',()=>{
  for(const state of ['composing','disposed','restarting','inputError','stopped']){
    const f=fixture();if(state==='stopped')f.panel.running=false;else f.panel[state]=true;
    f.panel.shortcut('Enter','\r');assert.equal(f.panel.operations.length,0,state);
  }

});

test('cloud web and alternate channels retain the HTTP terminal transport',()=>{
  for(const route of [{BridgeCloudWeb:true},{BridgeChannelLink:{alternate:true}}]){
    const f=fixture();Object.assign(f.context.window,route);
    f.context.URL=URL;f.context.location={href:'https://fixture.test/'};
    f.panel.workbench.csrf=()=> 'fixture-csrf';
    let sockets=0;f.context.WebSocket=class {constructor(){sockets++;}};
    f.panel.startDuplex();assert.equal(sockets,0);assert.equal(f.panel.duplex,undefined);
  }
});

test('duplex sends ordered resize and several inputs without waiting for each acknowledgement',()=>{
  const f=fixture(),sockets=[];
  class FakeSocket{
    static CONNECTING=0;static OPEN=1;
    constructor(url,protocols){this.url=url;this.protocols=protocols;this.readyState=0;this.bufferedAmount=0;this.sent=[];sockets.push(this);}
    send(value){this.sent.push(JSON.parse(value));}
    close(){this.readyState=3;this.onclose?.();}
  }
  f.context.WebSocket=FakeSocket;f.context.URL=URL;f.context.location={href:'http://local.example/'};
  f.panel.workbench.csrf=()=> 'csrf-secret';f.panel.layout=()=>{};
  f.panel.startDuplex();const socket=sockets[0];socket.readyState=1;socket.onopen();
  f.panel.queueChunk('你');f.panel.queueChunk('😀');f.panel.pendingResize={cols:100,rows:30};
  f.panel.flushDuplex();
  assert.deepEqual(socket.sent.map(item=>item.action),['resize','input']);
  assert.equal(socket.sent[1].data,'你😀');
  assert.equal(f.panel.operations.length,1);
  socket.onmessage({data:JSON.stringify({type:'ack',action:'input',id:f.panel.operations[0].id})});
  assert.equal(f.panel.operations.length,0);
});

test('lost duplex acknowledgement pauses input and explicit retry reuses its inputId over HTTP',async()=>{
  const f=fixture();
  class FakeSocket{
    static CONNECTING=0;static OPEN=1;
    constructor(){this.readyState=0;this.bufferedAmount=0;}
    send(){}
    close(){this.readyState=3;this.onclose?.();}
  }
  f.context.WebSocket=FakeSocket;f.context.URL=URL;f.context.location={href:'http://local.example/'};
  f.panel.workbench.csrf=()=> 'csrf-secret';f.panel.layout=()=>{};
  f.panel.startDuplex();const socket=f.panel.duplex;socket.readyState=1;socket.onopen();
  f.panel.queueChunk('danger\r');f.panel.flushDuplex();const inputId=f.panel.operations[0].id;
  socket.close();assert.equal(f.panel.inputError,true);assert.equal(f.panel.operations[0].id,inputId);
  f.panel.inputError=false;await f.panel.flush();
  assert.equal(f.requests[0].inputId,inputId);assert.equal(f.panel.operations.length,0);
});

test('opening duplex waits for an in-flight HTTP input before taking queued operations',async()=>{
  const f=fixture();let finishHttp;
  class FakeSocket{
    static CONNECTING=0;static OPEN=1;
    constructor(){this.readyState=0;this.bufferedAmount=0;this.sent=[];}
    send(value){this.sent.push(JSON.parse(value));}
    close(){this.readyState=3;this.onclose?.();}
  }
  f.context.WebSocket=FakeSocket;f.context.URL=URL;f.context.location={href:'http://local.example/'};
  f.panel.workbench.csrf=()=> 'csrf-secret';f.panel.layout=()=>{};
  f.panel.workbench.request=async(url,body)=>new Promise(resolve=>{finishHttp=resolve;f.requests.push(body);});
  f.panel.queueChunk('first');const flushing=f.panel.flush();await tick();
  f.panel.startDuplex();const socket=f.panel.duplex;socket.readyState=1;socket.onopen();
  assert.deepEqual(socket.sent,[]);
  finishHttp({});await flushing;
  assert.deepEqual(socket.sent,[]);assert.equal(f.panel.operations.length,0);
  f.panel.queueChunk('second');f.panel.flushDuplex();
  assert.equal(socket.sent[0].data,'second');
});

test('equal-cursor WebSocket state after an HTTP poll is acknowledged without duplicate rendering',()=>{
  const f=fixture(),applied=[];
  class FakeSocket{
    static CONNECTING=0;static OPEN=1;
    constructor(){this.readyState=0;this.bufferedAmount=0;this.sent=[];}
    send(value){this.sent.push(JSON.parse(value));}
    close(){this.readyState=3;this.onclose?.();}
  }
  f.context.WebSocket=FakeSocket;f.context.URL=URL;f.context.location={href:'http://local.example/'};
  f.panel.workbench.csrf=()=> 'csrf-secret';f.panel.layout=()=>{};
  f.panel.apply=(result,rendered)=>{applied.push(result);rendered?.();};
  f.panel.cursor=5;f.panel.startDuplex();const socket=f.panel.duplex;socket.readyState=1;socket.onopen();
  socket.onmessage({data:JSON.stringify({type:'state',seq:1,value:{output:'already shown',cursor:5,reset:false,running:false}})});
  assert.equal(applied[0].output,'');assert.equal(applied[0].running,false);
  assert.deepEqual(socket.sent,[{action:'rendered',seq:1}]);
});

test('failed upgrade falls back to HTTP and established socket reconnects after clean loss',async()=>{
  const f=fixture(),sockets=[];
  class FakeSocket{
    static CONNECTING=0;static OPEN=1;
    constructor(){this.readyState=0;this.bufferedAmount=0;this.sent=[];sockets.push(this);}
    send(value){this.sent.push(JSON.parse(value));}
    close(){this.readyState=3;this.onclose?.();}
  }
  f.context.WebSocket=FakeSocket;f.context.URL=URL;f.context.location={href:'http://local.example/'};
  f.panel.workbench.csrf=()=> 'csrf-secret';f.panel.layout=()=>{};
  f.panel.startDuplex();sockets[0].close();
  assert.equal(f.panel.duplexUnsupported,true);
  f.panel.queueChunk('fallback');await f.panel.flush();
  assert.equal(f.requests[0].data,'fallback');

  f.panel.duplexUnsupported=false;f.panel.startDuplex();sockets[1].readyState=1;sockets[1].onopen();
  sockets[1].close();assert.ok(f.panel.reconnectTimer);
  f.timers.get(f.panel.reconnectTimer).fn();
  assert.equal(sockets.length,3);
});

test('draft preflight reserves bracketed paste markers and keeps boundary overflow text',async()=>{
  const f=fixture();f.context.TextEncoder=TextEncoder;f.panel.layout=()=>{};
  let pasted=0;f.panel.term.paste=text=>{pasted++;f.panel.enqueue('\x1b[200~'+text+'\x1b[201~');};
  f.panel.operations=[{id:'pending',data:'x'.repeat(262131),sent:false}];
  f.panel.input.value='a';await f.panel.submit();
  assert.equal(pasted,0);assert.equal(f.panel.input.value,'a');
  f.panel.operations[0].data='x'.repeat(262130);
  await f.panel.submit();
  assert.equal(pasted,1);assert.equal(f.panel.input.value,'');
  assert.equal(f.panel.operations.reduce((total,entry)=>total+entry.data.length,0),262144);
});

test('WebSocket send failure retains the input and shows the unconfirmed state',()=>{
  const f=fixture();
  class FakeSocket{
    static CONNECTING=0;static OPEN=1;
    constructor(){this.readyState=0;this.bufferedAmount=0;}
    send(){throw Error('socket failed');}
    close(){this.readyState=3;this.onclose?.();}
  }
  f.context.WebSocket=FakeSocket;f.context.URL=URL;f.context.location={href:'http://local.example/'};
  f.panel.workbench.csrf=()=> 'csrf-secret';f.panel.layout=()=>{};
  f.panel.startDuplex();const socket=f.panel.duplex;socket.readyState=1;socket.onopen();
  f.panel.queueChunk('still pending');const id=f.panel.operations[0].id;f.panel.flushDuplex();
  assert.equal(f.panel.inputError,true);assert.equal(f.panel.operations[0].id,id);
  assert.match(f.panel.status.textContent,/输入结果未确认/);
});

test('new terminal notices have English translations',()=>{
  const dictionary=require('../web/shared/i18n.js').dictionary;
  for(const key of ['终端输入过长或队列已满；草稿仍在，请稍后重试','终端输入队列已满；这部分输入未发送',
                    '终端连接中断 · 输入结果未确认；重试不会重复输入。','终端连接失败']){
    assert.ok(dictionary[key]);
  }
});
