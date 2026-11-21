const {test}=require('node:test');
const assert=require('node:assert/strict');
const {QuotaContinuationToggle}=require('../web/shared/quota-continuation.js');

function surface(store){
  const input={checked:false,disabled:true},error={textContent:''},status={textContent:''};
  const toggle=new QuotaContinuationToggle({input,error,status,read:async()=>({...store}),
    write:async enabled=>{store.enabled=enabled;return {...store};}});
  return {input,error,status,toggle};
}
test('home explains pending tasks, retries and unknown outcomes',async()=>{
  const store={enabled:true,state:'waitingQuota',pending:2,checkedAt:1000};
  const s=surface(store);await s.toggle.refresh();
  assert.match(s.status.textContent,/等待额度可用/);assert.match(s.status.textContent,/待继续 2 个/);
  assert.match(s.status.textContent,/已继续 0 个/);assert.match(s.status.textContent,/结果待确认 0 个/);
  store.state='error';store.error='发送前核验暂未通过，尚未发送，将自动重试。';await s.toggle.refresh();
  assert.match(s.status.textContent,/自动重试/);assert.match(s.error.textContent,/尚未发送/);
  store.state='unknown';store.error='';await s.toggle.refresh();assert.match(s.status.textContent,/结果待确认/);
});
test('mobile, web and desktop read the same server switch after changes',async()=>{
  const store={enabled:false};
  const surfaces=[surface(store),surface(store),surface(store)];
  await Promise.all(surfaces.map(s=>s.toggle.refresh()));
  surfaces[0].input.checked=true;await surfaces[0].input.onchange();
  await Promise.all(surfaces.map(s=>s.toggle.refresh()));
  assert.ok(surfaces.every(s=>s.input.checked&&!s.input.disabled));
  surfaces[2].input.checked=false;await surfaces[2].input.onchange();
  await Promise.all(surfaces.map(s=>s.toggle.refresh()));
  assert.ok(surfaces.every(s=>!s.input.checked));
});
test('an older read cannot overwrite a newly saved switch',async()=>{
  const store={enabled:false},s=surface(store);
  let resolve;
  s.toggle.read=()=>new Promise(r=>{resolve=r;});
  const reading=s.toggle.refresh();
  s.input.checked=true;
  await s.input.onchange();
  resolve({enabled:false});await reading;
  assert.equal(s.input.checked,true);
});
test('failed writes reread the server and display read failures',async()=>{
  const s=surface({enabled:false});
  await s.toggle.refresh();
  s.toggle.write=async()=>{throw Error('write failed');};
  s.input.checked=true;await s.input.onchange();
  assert.equal(s.input.checked,false);
  s.toggle.read=async()=>{throw Error('gateway offline');};
  await s.toggle.refresh();
  assert.equal(s.input.disabled,true);
  assert.equal(s.error.textContent,'gateway offline');
});
test('hidden or logged-out surfaces ignore delayed server responses',async()=>{
  const s=surface({enabled:true});let visible=true,resolve;
  s.toggle.visible=()=>visible;s.toggle.read=()=>new Promise(r=>{resolve=r;});
  const reading=s.toggle.refresh();visible=false;resolve({enabled:true});await reading;
  assert.equal(s.input.checked,false);
});
test('task exclusions serialize settings writes and ignore failures after leaving the page',async()=>{
  const s=surface({enabled:true}),doc={createElement:()=>({children:[],append(...children){this.children.push(...children);}})};
  const tasks={ownerDocument:doc,children:[],replaceChildren(){this.children=[];},append(child){this.children.push(child);}};
  let visible=true,reject,writes=0;
  Object.assign(s.toggle,{tasks,visible:()=>visible,exclude:()=>{writes++;return new Promise((_,fail)=>reject=fail);}});
  s.toggle.paint({enabled:true,tasks:[{threadId:'one',title:'One'},{threadId:'two',title:'Two'}]});
  const first=tasks.children[0].children[0],second=tasks.children[1].children[0];
  first.checked=true;const pending=first.onchange();
  second.checked=true;await second.onchange();assert.equal(writes,1);assert.equal(second.checked,false);
  assert.equal(s.input.disabled,true);
  visible=false;reject(Error('old failure'));await pending;
  assert.equal(s.error.textContent,'');assert.equal(s.toggle.busy,false);
});
