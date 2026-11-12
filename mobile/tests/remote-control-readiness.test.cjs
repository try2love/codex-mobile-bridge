'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const source=fs.readFileSync(path.join(__dirname,'../shared/web/mobile-ui.js'),'utf8');
function fixture({open=false,value={}}={}){
  class Element{
    constructor(tag){this.tagName=tag;this.children=[];this.hidden=false;this.events={};}
    append(...children){this.children.push(...children);for(const child of children)child.parentNode=this;}
    insertBefore(child){this.append(child);}
    querySelector(){return null;}
    addEventListener(name,fn){this.events[name]=fn;}
    setAttribute(name,value){this[name]=value;}
  }
  const app=new Element('main'),dialog=new Element('dialog'),observers=[],events={},timers=new Map(),calls=[];
  dialog.open=open;let next=0;
  const document={styleSheets:[{href:'https://gateway.test/style.css',cssRules:[],insertRule(){}}],
    documentElement:{lang:'zh',classList:{contains:()=>false,add(){},toggle(){}}},
    createElement:tag=>new Element(tag),getElementById:id=>({'app':app,'appearance-dialog':dialog}[id]),
    querySelector:selector=>selector==='dialog[open]'&&dialog.open?dialog:null,
    addEventListener:(name,fn)=>events[name]=fn};
  const window={addEventListener(){},prompt:(name,action)=>{
    if(!name.startsWith('codexbridge-uu:'))return null;
    calls.push(action);return value===null?null:JSON.stringify(value);
  }};
  vm.runInNewContext(source,{document,window,URL,location:{origin:'https://gateway.test'},
    setTimeout:fn=>{timers.set(++next,fn);return next;},clearTimeout:id=>timers.delete(id),
    MutationObserver:class{constructor(fn){this.fn=fn;}observe(target){observers.push({target,fn:this.fn});}}});
  const section=dialog.children[0],option=section.children[1],toggle=option.children[1],hint=option.children[0].children[1];
  return {app,dialog,section,toggle,hint,calls,window,events,
    change:next=>{value=next;window.BridgeUURemote.refresh();},
    open:()=>{dialog.open=true;observers.filter(x=>x.target===dialog).forEach(x=>x.fn());},
    expire:()=>{const callbacks=[...timers.values()];timers.clear();callbacks.forEach(fn=>fn());},
    retry:()=>section.children.find(x=>x.textContent==='重新检查')};
}
test('settings are visible and disabled while native identity is pending',()=>{
  const f=fixture();f.open();assert.equal(f.section.hidden,false);assert.equal(f.toggle.disabled,true);
  assert.match(f.hint.textContent,/正在确认/);
  f.change({supported:true,enabled:true,bound:true});
  assert.equal(f.toggle.disabled,false);assert.equal(f.toggle.checked,true);
});
test('late script injection renders an already open settings dialog immediately',()=>{
  const f=fixture({open:true});assert.equal(f.section.hidden,false);assert.equal(f.toggle.disabled,true);
  assert.equal(f.section.children[0].textContent,'远程控制');
});
test('slow identity query ends waiting and exposes explicit retry without enabling control',()=>{
  const f=fixture({open:true});f.expire();assert.match(f.hint.textContent,/未能确认/);
  assert.equal(f.toggle.disabled,true);assert.equal(f.retry().hidden,false);
  f.retry().onclick({isTrusted:false});assert.equal(f.calls.includes('retry'),false);
  f.retry().onclick({isTrusted:true});assert.equal(f.calls.at(-1),'retry');
});
test('unavailable or unsupported destinations retain the module and disable its controls',()=>{
  const f=fixture({open:true,value:{supported:true,enabled:true,bound:true}});
  f.change({availability:'unsupported',supported:false});
  assert.equal(f.section.hidden,false);assert.equal(f.toggle.disabled,true);assert.match(f.hint.textContent,/暂不支持/);
  f.change({availability:'unavailable'});assert.match(f.hint.textContent,/未能确认/);
  f.app.hidden=true;f.change({});assert.equal(f.section.hidden,true);
});
test('unknown settings never create a remote-control menu or accept synthetic changes',()=>{
  const f=fixture({open:true});assert.deepEqual(Array.from(f.window.BridgeUURemote.menu({})),[]);
  f.toggle.onchange({isTrusted:true});assert.equal(f.calls.includes('enable'),false);
  f.change({supported:true,enabled:true});f.toggle.onchange({isTrusted:false});assert.equal(f.calls.includes('enable'),false);
});
