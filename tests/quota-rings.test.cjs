const {test}=require('node:test');
const assert=require('node:assert/strict');
const {AccountQuotaRings,accountQuotaWindows}=require('../web/features/accounts/quota-rings.js');

class Node {
  constructor(tag){this.tagName=tag;this.children=[];this.attributes={};this.textContent='';this.hidden=false;this.open=false;
    const classes=new Set();this.classList={toggle:(name,on)=>on?classes.add(name):classes.delete(name),contains:name=>classes.has(name)};
    this.dataset={};this.style={setProperty:(name,value)=>{this.style[name]=value;}};
  }
  append(...children){this.children.push(...children);}
  setAttribute(name,value){this.attributes[name]=value;}
  showModal(){this.open=true;}
  close(){this.open=false;}
}
const quota=(primary=66,secondary=21)=>({visible:true,accountKey:'account',updatedAt:1000,limits:[{id:'codex',windows:[
  {kind:'primary',remainingPercent:primary,windowDurationMins:300,resetsAt:2000},
  {kind:'secondary',remainingPercent:secondary,windowDurationMins:10080,resetsAt:3000}]}]});
function fixture(value=quota()){
  let scope={key:'first-account',host:'local'},visible=true,now=1000,calls=[];
  const doc={createElement:tag=>new Node(tag),body:new Node('body')};
  const rings=new AccountQuotaRings({document:doc,context:()=>scope,visible:()=>visible,now:()=>now,
    read:async(context,force)=>{calls.push({context,force});return value;}});
  return {rings,calls,setScope:v=>{scope=v;},setVisible:v=>{visible=v;},advance:n=>{now+=n;}};
}
test('two rings show primary and weekly remaining percentages, including real zero',async()=>{
  const {rings}=fixture(quota(66.6,0));await rings.refresh();
  assert.equal(rings.rings[0].percent.textContent,'66.6%');assert.equal(rings.rings[1].percent.textContent,'0%');
  assert.equal(rings.rings[0].label.textContent,'5小时');assert.equal(rings.rings[1].label.textContent,'每周');
  assert.equal(rings.rings[1].button.classList.contains('low'),true);
});
test('review quotas and unordered windows do not change which percentages are shown',()=>{
  const value=quota();value.limits[0].windows.reverse();
  value.limits.unshift({id:'code-review',windows:[{kind:'primary',remainingPercent:0}]});
  assert.deepEqual(accountQuotaWindows(value).map(w=>w.percent),[66,21]);
});
test('missing or invalid data shows an unknown ring rather than zero quota',async()=>{
  const {rings}=fixture(quota(null,Number.NaN));await rings.refresh();
  assert.equal(rings.rings[0].percent.textContent,'—');assert.equal(rings.rings[1].percent.textContent,'—');
  assert.equal(rings.rings[0].button.classList.contains('unknown'),true);
});
test('foreground reads use five-minute cache; explicit refresh requests fresh quota',async()=>{
  const f=fixture();await f.rings.refresh();await f.rings.refresh();assert.equal(f.calls.length,1);
  f.advance(300001);await f.rings.refresh();assert.equal(f.calls.length,2);
  await f.rings.refresh(true);assert.equal(f.calls.at(-1).force,true);
});
test('hidden or unsupported-host controls never request quota',async()=>{
  const f=fixture();f.setVisible(false);await f.rings.refresh();assert.equal(f.calls.length,0);
  f.setScope(null);f.setVisible(true);await f.rings.refresh();assert.equal(f.calls.length,0);
  assert.equal(f.rings.root.hidden,true);
});
test('account changes discard stale responses and immediately clear old percentages',async()=>{
  const f=fixture();let resolve;
  f.rings.read=()=>new Promise(r=>{resolve=r;});const old=f.rings.refresh();
  f.setScope({key:'second-account',host:'codex:profile'});
  f.rings.read=async()=>quota(12,34);await f.rings.refresh();resolve(quota(99,99));await old;
  assert.equal(f.rings.rings[0].percent.textContent,'12%');
  assert.equal(f.rings.rings[1].percent.textContent,'34%');
});
test('failed reads label retained values stale and retry without a request loop',async()=>{
  const f=fixture();await f.rings.refresh();
  f.rings.read=async()=>{throw Error('offline');};await f.rings.refresh(true);
  assert.equal(f.rings.rings[0].percent.textContent,'66%');assert.equal(f.rings.root.classList.contains('stale'),true);
  assert.match(f.rings.message(),/数据未更新/);
  let reads=0;f.rings.read=async()=>{reads++;return quota();};await f.rings.refresh();assert.equal(reads,0);
  f.advance(60001);await f.rings.refresh();assert.equal(reads,1);
});
test('tapping a ring shows used quota, reset time and a manual refresh control',async()=>{
  const f=fixture();await f.rings.refresh();f.rings.rings[0].button.onclick();
  assert.equal(f.rings.dialog.open,true);assert.match(f.rings.detailsUsage.textContent,/剩余额度 66%.*已用 34%/);
  assert.match(f.rings.detailsReset.textContent,/恢复时间/);
  await f.rings.refreshButton.onclick();assert.equal(f.calls.at(-1).force,true);
});
test('API access is explained without inventing official quota',async()=>{
  const {rings}=fixture({visible:false,loginType:'api'});await rings.refresh();
  assert.equal(rings.rings[0].percent.textContent,'—');assert.match(rings.message(),/API 接入不提供官方额度/);
});
