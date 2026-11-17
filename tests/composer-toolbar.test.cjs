'use strict';
const test=require('node:test'),assert=require('node:assert/strict');
const {normalizeComposerLayout,defaultComposerLayout,normalizeComposerDisplay,composerToolIds}=require('../web/features/chat/composer-preferences.js');
test('hidden tools are stored in the panel while panel and submit remain accessible',()=>{
  const {normalizeComposerPreferences}=require('../web/features/chat/composer-preferences.js');
  const layout=defaultComposerLayout(),value=normalizeComposerPreferences({layout,display:{model:'hidden',context:'hidden',more:'hidden',submit:'hidden'}});
  assert.equal(value.display.model,'hidden');assert.equal(value.display.context,'hidden');
  assert.equal(value.display.more,'icon');assert.equal(value.display.submit,'both');
  assert.ok(value.layout.more.includes('model'));assert.ok(value.layout.more.includes('context'));
  assert.ok(!value.layout.bottomLeft.includes('model'));assert.ok(!value.layout.topRight.includes('context'));
  assert.ok(layout.bottomLeft.includes('model'));assert.ok(layout.topRight.includes('context'));
  assert.deepEqual(normalizeComposerPreferences(value),value);
});
test('panel entry survives older or inaccessible layouts and supports all four toolbar zones',()=>{
  for(const zone of ['topLeft','topRight','bottomLeft','bottomRight']){
    const layout=defaultComposerLayout();layout.topLeft=layout.topLeft.filter(id=>id!=='more');layout[zone].push('more');
    assert.equal(normalizeComposerLayout(layout)[zone].filter(id=>id==='more').length,1);
  }
  const old=defaultComposerLayout();old.topLeft=old.topLeft.filter(id=>id!=='more');
  assert.deepEqual(normalizeComposerLayout(old).topLeft,['more','permissions']);
  old.more.push('more','more');const repaired=normalizeComposerLayout(old);
  assert.ok(repaired.topLeft.includes('more'));assert.ok(!repaired.more.includes('more'));
});
test('toolbar preferences filter invalid duplicates and keep each tool accessible',()=>{
  const input={topLeft:['skills','skills','__proto__','missing'],topRight:['context'],bottomLeft:['model'],bottomRight:[],more:['skills','goal']};
  const normalized=normalizeComposerLayout(input);
  assert.deepEqual(normalized.topLeft,['more','skills']);assert.deepEqual(normalized.bottomLeft,['model']);
  assert.deepEqual(Object.values(normalized).flat().sort(),[...composerToolIds].sort());
  assert.ok(normalized.more.includes('sendMode'));assert.ok(normalized.more.includes('permissions'));
  assert.equal(input.topLeft.length,4);
});
test('display styles are validated per tool and preserve independent defaults',()=>{
  const value=normalizeComposerDisplay({model:'icon',permissions:'both',skills:'text',context:'invalid',unknown:'both'});
  assert.equal(value.model,'icon');assert.equal(value.permissions,'both');assert.equal(value.skills,'text');assert.equal(value.context,'icon');assert.equal(value.sendMode,'text');assert.equal(value.unknown,undefined);
  value.goal='both';assert.equal(normalizeComposerDisplay().goal,'icon');
});
test('malformed or unavailable local preferences use an independent usable default',()=>{
  for(const value of [null,[],{topLeft:'model'},Object.fromEntries(Object.keys(defaultComposerLayout()).map(k=>[k,null]))])assert.deepEqual(normalizeComposerLayout(value),defaultComposerLayout());
  const first=defaultComposerLayout();first.topLeft.length=0;assert.ok(defaultComposerLayout().topLeft.length);
});

test('collapse defaults to the top right and supports a user-selected position',()=>{
  assert.deepEqual(defaultComposerLayout().topRight,['context','collapse']);
  const layout=defaultComposerLayout();layout.topRight=['context'];
  layout.more.push('collapse');assert.ok(normalizeComposerLayout(layout).more.includes('collapse'));
});

test('independent send mode defaults to More and keeps explicitly saved positions',()=>{
 const layout=defaultComposerLayout();assert.ok(layout.more.includes('sendMode'));assert.ok(!layout.bottomRight.includes('sendMode'));
 layout.more=layout.more.filter(id=>id!=='sendMode');layout.topRight.push('sendMode');assert.ok(normalizeComposerLayout(layout).topRight.includes('sendMode'));
});

const {deriveComposerAction,ComposerPreferences,composerPreferencesKey}=require('../web/features/chat/composer-preferences.js');
test('effective send mode follows runtime without changing the global preference',()=>{
  for(const preferred of ['send','queue','steer']){
    const idle=deriveComposerAction({running:false,hasDraft:true},preferred);
    assert.equal(idle.mode,'send');assert.equal(idle.label,'发送');assert.equal(idle.choices,false);
    const active=deriveComposerAction({running:true,hasDraft:true},preferred);
    assert.equal(active.mode,preferred);assert.equal(active.choices,true);
    const empty=deriveComposerAction({running:true,hasDraft:false},preferred);
    assert.equal(empty.stop,true);assert.equal(empty.sendDisabled,true);
  }
});
test('capability fallback and pending actions keep controls safe',()=>{
  assert.equal(deriveComposerAction({running:false,hasDraft:true,stopEnabled:true}).stopDisabled,true);
  assert.equal(deriveComposerAction({running:true,hasDraft:true,availableModes:['send','queue']},'steer').mode,'queue');
  assert.equal(deriveComposerAction({running:false,hasDraft:true,availableModes:['queue']},'queue').sendDisabled,true);
  const busy=deriveComposerAction({running:true,hasDraft:true,pending:true},'steer');
  assert.equal(busy.sendDisabled,true);assert.equal(busy.stopDisabled,true);assert.equal(busy.choicesDisabled,true);
  assert.equal(deriveComposerAction({running:true,hasDraft:true,sendEnabled:false},'queue').sendDisabled,true);
});
function preferenceFixture(){
 const rows=new Map(),listeners=new Map();let writes=0,fail=false;
 const storage={getItem:key=>rows.get(key)||null,setItem(key,value){if(fail)throw Error('full');writes++;rows.set(key,value);}};
 const events={addEventListener:(key,fn)=>listeners.set(key,fn),removeEventListener:key=>listeners.delete(key)};
 return {store:new ComposerPreferences(storage,events),storage,rows,listeners,writes:()=>writes,fail:()=>fail=true};
}
test('preferences persist globally, reset only layout and publish committed changes once',()=>{
 const f=preferenceFixture();let changes=0;f.store.subscribe(()=>changes++);
 f.store.update({followUpMode:'steer'});assert.equal(changes,1);f.store.update({followUpMode:'steer'});assert.equal(f.writes(),1);
 const second=new ComposerPreferences(f.storage);assert.equal(second.value.followUpMode,'steer');
 const layout=defaultComposerLayout();layout.topLeft.push(layout.more.shift());f.store.update({layout,display:{model:'both'}});
 f.store.resetLayout();assert.deepEqual(f.store.value.layout,defaultComposerLayout());assert.equal(f.store.value.followUpMode,'steer');assert.equal(f.store.value.display.model,'text');
 assert.throws(()=>f.store.value.layout.topLeft.push('model'),TypeError);
});
test('failed persistence leaves all views at the last committed settings',()=>{
 const f=preferenceFixture(),before=f.store.value;let changes=0;f.store.subscribe(()=>changes++);f.fail();
 assert.throws(()=>f.store.update({followUpMode:'steer'}),/保存失败/);assert.equal(f.store.value,before);assert.equal(changes,0);
});
test('storage events refresh shared preferences and clear returns defaults',()=>{
 const f=preferenceFixture();f.rows.set(composerPreferencesKey,JSON.stringify({followUpMode:'steer'}));f.listeners.get('storage')({key:composerPreferencesKey});assert.equal(f.store.value.followUpMode,'steer');
 f.rows.clear();f.listeners.get('storage')({key:null});assert.equal(f.store.value.followUpMode,'queue');f.store.dispose();assert.equal(f.listeners.size,0);
});

test('Skill defaults to the add menu while retaining a customizable toolbar position',()=>{
 const {composerTools}=require('../web/features/chat/composer-preferences.js');
 assert.equal(composerTools.find(tool=>tool.id==='skills').addMenu,true);
 assert.ok(defaultComposerLayout().more.includes('skills'));assert.ok(!defaultComposerLayout().topLeft.includes('skills'));
 const layout=defaultComposerLayout();layout.more=layout.more.filter(id=>id!=='skills');layout.topLeft.push('skills');
 assert.deepEqual(normalizeComposerLayout(layout).topLeft,['more','permissions','skills']);
});

test('submit display is customizable while its position stays outside movable tools',()=>{
 for(const mode of ['icon','text','both'])assert.equal(normalizeComposerDisplay({submit:mode}).submit,mode);
 assert.equal(normalizeComposerDisplay({submit:'invalid'}).submit,'both');
 const layout=defaultComposerLayout();layout.topLeft.push('submit');layout.more.push('submit');
 assert.deepEqual(normalizeComposerLayout(layout),defaultComposerLayout());
 const f=preferenceFixture();f.store.update({display:{submit:'icon'}});assert.equal(new ComposerPreferences(f.storage).value.display.submit,'icon');
 f.store.resetLayout();assert.equal(f.store.value.display.submit,'both');
});

test('layout presets keep every tool accessible, persist and retain the send preference',()=>{
 const {composerPreset}=require('../web/features/chat/composer-preferences.js'),f=preferenceFixture();
 f.store.update({followUpMode:'steer',display:{permissions:'hidden'}});
 for(const id of ['single','default']){
  f.store.applyPreset(id);const value=f.store.value;
  assert.deepEqual(Object.values(value.layout).flat().sort(),[...composerToolIds].sort());
  assert.equal(value.followUpMode,'steer');assert.equal(value.display.permissions,'icon');
  assert.equal(value.display.collapse,id==='single'?'hidden':'icon');
  assert.deepEqual(new ComposerPreferences(f.storage).value,value);
  assert.deepEqual(value.layout,composerPreset(id).layout);
  assert.equal(value.layout.topLeft.length===0,id==='single');
  assert.equal(value.layout.topRight.length===0,id==='single');
 }
 const preset=composerPreset('single');preset.layout.bottomLeft.length=0;
 assert.deepEqual(composerPreset('single').layout.bottomLeft,['more','model']);
 assert.deepEqual(composerPreset('single').layout.bottomRight,['context','attachments']);
 assert.throws(()=>f.store.applyPreset('invalid'),/Unknown composer preset/);
});

test('moving tools across every zone preserves each tool exactly once and leaves the source untouched',()=>{
 const {moveComposerTool,normalizeComposerPreferences,composerZones}=require('../web/features/chat/composer-preferences.js');
 const original=normalizeComposerPreferences({followUpMode:'steer',display:{model:'both',submit:'text'}});
 for(const id of composerToolIds)for(const zone of composerZones){
  const next=normalizeComposerPreferences(moveComposerTool(original,id,zone,0));
  assert.deepEqual(Object.values(next.layout).flat().sort(),[...composerToolIds].sort());
  assert.equal(next.followUpMode,'steer');assert.deepEqual(next.display,original.display);
  if(id==='more'&&zone==='more')assert.deepEqual(next,original);
  else assert.equal(next.layout[zone][0],id);
 }
 assert.deepEqual(original.layout,defaultComposerLayout());
});

test('moving a hidden tool back restores its display default and clamps insertion to the destination',()=>{
 const f=preferenceFixture();f.store.update({display:{model:'hidden',context:'both'}});
 f.store.moveTool('model','bottomRight',99);
 assert.deepEqual(f.store.value.layout.bottomRight,['model']);assert.equal(f.store.value.display.model,'text');
 assert.equal(f.store.value.display.context,'both');
 f.store.moveTool('context','bottomRight',-1);assert.deepEqual(f.store.value.layout.bottomRight,['context','model']);
 f.store.moveTool('context','bottomRight',99);assert.deepEqual(f.store.value.layout.bottomRight,['model','context']);
 f.store.moveTool('context','more',0);assert.equal(f.store.value.display.context,'both');
 assert.deepEqual(new ComposerPreferences(f.storage).value,f.store.value);
});

test('fixed controls and unknown tools or destinations leave saved preferences unchanged',()=>{
 const f=preferenceFixture(),before=f.store.value;
 for(const [id,zone] of [['more','more'],['submit','topLeft'],['unknown','topLeft'],['model','unknown']])f.store.moveTool(id,zone,0);
 assert.equal(f.writes(),0);assert.equal(f.store.value,before);
});

test('failed tool movement preserves the committed layout, display and subscribers',()=>{
 const f=preferenceFixture();f.store.update({display:{model:'hidden'}});
 const before=f.store.value;let changes=0;f.store.subscribe(()=>changes++);f.fail();
 assert.throws(()=>f.store.moveTool('model','topLeft',0),/保存失败/);
 assert.equal(f.store.value,before);assert.equal(changes,0);
});
