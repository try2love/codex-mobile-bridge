'use strict';
const test=require('node:test'),assert=require('node:assert/strict');
class Node{
  constructor(tag){Object.assign(this,{tag,children:[],attributes:{},dataset:{},style:{setProperty:(key,value)=>{this.style[key]=value;}}});}
  append(...children){this.children.push(...children);}replaceChildren(...children){this.children=children;}setAttribute(k,v){this.attributes[k]=v;}
}
global.document={createElement:tag=>new Node(tag)};
const {QuotaDisplay:Q,AccountQuotaIndicator}=require('../web/features/accounts/quota-display.js');
const windows=[{kind:'primary',remainingPercent:70,windowDurationMins:300,resetsAt:2000000000},{kind:'secondary',remainingPercent:0,windowDurationMins:10080}];
const quota=(key='a',percent=70)=>({visible:true,accountKey:key,updatedAt:100,limits:[{id:'codex',windows:[{...windows[0],remainingPercent:percent},windows[1]]}]});
const flatten=node=>[node,...node.children.flatMap(flatten)];
test('shared window projection supports Codex, Claude, daily token buckets and unknown data',()=>{
  assert.deepEqual(Q.windows(windows).map(Q.label),['5h','7d']);
  assert.equal(Q.windows([{windowDurationMins:1440,remainingPercent:45,remaining:9,total:20,unit:'tokens'}])[0].percent,45);
  for(const invalid of [undefined,null,NaN,Infinity,-1,101,'30'])assert.equal(Q.windows([{remainingPercent:invalid}])[0].percent,null);
  assert.equal(Q.windows([windows[1]])[0].percent,0);
});
test('weekly-only models keep their aligned column; detail includes exact quantities and safe text',()=>{
  const slots=Q.columns([windows]),badges=Q.badges([windows[1]],{slots});assert.match(badges.children[0].className,/placeholder/);assert.equal(badges.children[1].children.at(-1).textContent,'0%');
  const root=Q.pools([{name:'<script>fixture</script>',windows:[{windowDurationMins:1440,remaining:9,total:20,unit:'tokens',remainingPercent:45}],models:['M']}]);
  const text=flatten(root).map(row=>row.textContent||'').join('\n');assert.match(text,/9 \/ 20 tokens/);assert.match(text,/<script>fixture<\/script>/);assert.match(text,/共用模型/);
});
test('compact toolbar rings carry accessible details without numeric text clutter',()=>{
  const root=Q.badges(windows,{compact:true});assert.equal(root.children.length,2);assert.match(root.children[0].attributes['aria-label'],/5h.*70%/);assert.equal(root.children[0].children.length,1);
});
test('account indicator scopes pending responses and hides unsupported or background contexts',async()=>{
  let scope={key:'a'},visible=true,calls=0,resolve;const indicator=new AccountQuotaIndicator({context:()=>scope,visible:()=>visible,read:()=>{calls++;return new Promise(r=>resolve=r);}});
  const old=indicator.refresh();scope={key:'b'};indicator.read=async()=>quota('b',12);await indicator.refresh();resolve(quota('a',99));await old;
  assert.equal(indicator.value.accountKey,'b');assert.equal(Q.accountWindows(indicator.value)[0].remainingPercent,12);
  scope=null;await indicator.refresh();assert.equal(indicator.root.hidden,true);assert.equal(indicator.value,null);
  scope={key:'c'};visible=false;await indicator.refresh();assert.equal(calls,1);
});
test('failed refresh preserves same-account time, throttles retry, and discards a different account',async()=>{
  let now=1000,calls=0;const indicator=new AccountQuotaIndicator({context:()=>({key:'a'}),visible:()=>true,now:()=>now,read:async()=>{calls++;return quota();}});
  await indicator.refresh();await indicator.refresh();assert.equal(calls,1);
  indicator.read=async()=>({error:'offline',accountKey:'a'});await indicator.refresh(true);assert.equal(indicator.value.updatedAt,100);assert.match(indicator.root.title,/数据尚未更新/);
  indicator.read=async()=>{calls++;return quota();};await indicator.refresh();assert.equal(calls,1);now+=60001;await indicator.refresh();assert.equal(calls,2);
  indicator.read=async()=>({error:'offline',accountKey:'b'});await indicator.refresh(true);assert.equal(Q.accountWindows(indicator.value).length,0);
});

test('native quota pools replace legacy credits while no-pool accounts keep their fallback',()=>{
  const root=new Node('div');
  Q.summary(root,{groups:[{name:'Shared',windows}],credits:'Prompt: 100 / Flow: 200'});
  assert.equal(flatten(root).some(row=>row.className==='quota-credits'),false);
  assert.equal(flatten(root).filter(row=>row.className==='quota-detail').length,2);
  Q.summary(root,{groups:[],credits:'Prompt: 100 / Flow: 200'});
  assert.equal(flatten(root).find(row=>row.className==='quota-credits').textContent,'Prompt: 100 / Flow: 200');
});
