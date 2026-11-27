'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const {
  ComposerPreferences,
  composerPreferencesKey,
  composerToolIds,
  composerZones,
  normalizeComposerPreferences,
  normalizeComposerLayout,
  normalizeComposerDisplay,
  defaultComposerLayout,
  composerPreset,
  moveComposerTool,
} = require('../web/features/chat/composer-preferences.js');
const { deriveComposerAction } = require('../web/features/chat/composer-state.js');

test('configuration repairs invalid layouts and styles without losing or duplicating tools', () => {
  const input = {
    topLeft: ['skills', 'skills', '__proto__'],
    topRight: [],
    bottomLeft: ['model'],
    bottomRight: [],
    more: ['skills'],
  };
  const layout = normalizeComposerLayout(input);
  assert.deepEqual(Object.values(layout).flat().sort(), [...composerToolIds].sort());
  assert.ok(layout.topLeft.includes('more'));
  assert.equal(input.topLeft.length, 3);
  const value = normalizeComposerPreferences({
    layout,
    display: {
      model: 'hidden',
      more: 'hidden',
      submit: 'hidden',
      context: 'invalid',
      unknown: 'both',
    },
    followUpMode: 'send',
  });
  assert.ok(value.layout.more.includes('model'));
  assert.equal(value.display.more, 'icon');
  assert.equal(value.display.submit, 'both');
  assert.equal(value.display.context, 'icon');
  assert.equal(value.display.unknown, undefined);
  assert.equal(value.followUpMode, 'queue');
  for (const malformed of [null, [], { topLeft: 'model' }])
    assert.deepEqual(normalizeComposerLayout(malformed), defaultComposerLayout());
  const fresh = defaultComposerLayout();
  fresh.topLeft.length = 0;
  assert.ok(defaultComposerLayout().topLeft.length);
});

test('effective send mode follows runtime without changing the global preference', () => {
  for (const preferred of ['send', 'queue', 'steer']) {
    const idle = deriveComposerAction({ running: false, hasDraft: true }, preferred);
    assert.equal(idle.mode, 'send');
    assert.equal(idle.label, '发送');
    assert.equal(idle.choices, false);
    const active = deriveComposerAction({ running: true, hasDraft: true }, preferred);
    assert.equal(active.mode, preferred === 'send' ? 'queue' : preferred);
    assert.equal(active.choices, true);
    const empty = deriveComposerAction({ running: true, hasDraft: false }, preferred);
    assert.equal(empty.stop, true);
    assert.equal(empty.sendDisabled, true);
  }
  assert.equal(
    deriveComposerAction({ running: false, hasDraft: true, stopEnabled: true }).stopDisabled,
    true,
  );
  assert.equal(
    deriveComposerAction(
      { running: true, hasDraft: true, availableModes: ['send', 'queue'] },
      'steer',
    ).mode,
    'queue',
  );
  assert.equal(
    deriveComposerAction({ running: false, hasDraft: true, availableModes: ['queue'] }, 'queue')
      .sendDisabled,
    true,
  );
  const busy = deriveComposerAction({ running: true, hasDraft: true, pending: true }, 'steer');
  assert.equal(busy.sendDisabled, true);
  assert.equal(busy.stopDisabled, true);
  assert.equal(busy.choicesDisabled, true);
  assert.equal(
    deriveComposerAction({ running: true, hasDraft: true, sendEnabled: false }, 'queue')
      .sendDisabled,
    true,
  );
  assert.equal(
    deriveComposerAction({ running: true, hasDraft: true, availableModes: ['send'] }).sendDisabled,
    true,
  );
});

function preferenceFixture() {
  const rows = new Map(),
    listeners = new Map();
  let writes = 0,
    fail = false;
  const storage = {
    getItem: (key) => rows.get(key) || null,
    setItem(key, value) {
      if (fail) throw Error('full');
      writes++;
      rows.set(key, value);
    },
  };
  const events = {
    addEventListener: (key, fn) => listeners.set(key, fn),
    removeEventListener: (key) => listeners.delete(key),
  };
  return {
    store: new ComposerPreferences(storage, events),
    storage,
    rows,
    listeners,
    writes: () => writes,
    fail: () => (fail = true),
  };
}
test('preferences persist globally, reset only layout and publish committed changes once', () => {
  const f = preferenceFixture();
  let changes = 0;
  f.store.subscribe(() => changes++);
  f.store.update({ followUpMode: 'steer' });
  assert.equal(changes, 1);
  f.store.update({ followUpMode: 'steer' });
  assert.equal(f.writes(), 1);
  const second = new ComposerPreferences(f.storage);
  assert.equal(second.value.followUpMode, 'steer');
  const layout = defaultComposerLayout();
  layout.topLeft.push(layout.more.shift());
  f.store.update({ layout, display: { model: 'both' } });
  f.store.resetLayout();
  assert.deepEqual(f.store.value.layout, defaultComposerLayout());
  assert.equal(f.store.value.followUpMode, 'steer');
  assert.equal(f.store.value.display.model, 'text');
  assert.throws(() => f.store.value.layout.topLeft.push('model'), TypeError);
});
test('failed persistence leaves all views at the last committed settings', () => {
  const f = preferenceFixture(),
    before = f.store.value;
  let changes = 0;
  f.store.subscribe(() => changes++);
  f.fail();
  assert.throws(() => f.store.update({ followUpMode: 'steer' }), /保存失败/);
  assert.equal(f.store.value, before);
  assert.equal(changes, 0);
  assert.throws(() => f.store.moveTool('model', 'topRight', 0), /保存失败/);
  assert.equal(f.store.value, before);
  assert.equal(changes, 0);
});
test('storage events refresh shared preferences and clear returns defaults', () => {
  const f = preferenceFixture();
  f.rows.set(composerPreferencesKey, JSON.stringify({ followUpMode: 'steer' }));
  f.listeners.get('storage')({ key: composerPreferencesKey });
  assert.equal(f.store.value.followUpMode, 'steer');
  f.rows.clear();
  f.listeners.get('storage')({ key: null });
  assert.equal(f.store.value.followUpMode, 'queue');
  f.store.dispose();
  assert.equal(f.listeners.size, 0);
});
test('layout presets keep every tool accessible, persist and retain the send preference', () => {
  const { composerPreset } = require('../web/features/chat/composer-preferences.js'),
    f = preferenceFixture();
  f.store.update({ followUpMode: 'steer', display: { permissions: 'hidden' } });
  for (const id of ['single', 'default']) {
    f.store.applyPreset(id);
    const value = f.store.value;
    assert.deepEqual(Object.values(value.layout).flat().sort(), [...composerToolIds].sort());
    assert.equal(value.followUpMode, 'steer');
    assert.equal(value.display.permissions, 'icon');
    assert.equal(value.display.collapse, id === 'single' ? 'hidden' : 'icon');
    assert.deepEqual(new ComposerPreferences(f.storage).value, value);
    assert.deepEqual(value.layout, composerPreset(id).layout);
    assert.equal(value.layout.topLeft.length === 0, id === 'single');
    assert.equal(value.layout.topRight.length === 0, id === 'single');
  }
  const preset = composerPreset('single');
  preset.layout.bottomLeft.length = 0;
  assert.deepEqual(composerPreset('single').layout.bottomLeft, ['more', 'model', 'quota']);
  assert.deepEqual(composerPreset('single').layout.bottomRight, ['context', 'attachments']);
  assert.throws(() => f.store.applyPreset('invalid'), /Unknown composer preset/);
});
test('moving tools across every zone preserves each tool exactly once and leaves the source untouched', () => {
  const {
    moveComposerTool,
    normalizeComposerPreferences,
    composerZones,
  } = require('../web/features/chat/composer-preferences.js');
  const original = normalizeComposerPreferences({
    followUpMode: 'steer',
    display: { model: 'both', submit: 'text' },
  });
  for (const id of composerToolIds)
    for (const zone of composerZones) {
      const next = normalizeComposerPreferences(moveComposerTool(original, id, zone, 0));
      assert.deepEqual(Object.values(next.layout).flat().sort(), [...composerToolIds].sort());
      assert.equal(next.followUpMode, 'steer');
      assert.deepEqual(next.display, original.display);
      if (id === 'more' && zone === 'more') assert.deepEqual(next, original);
      else assert.equal(next.layout[zone][0], id);
    }
  assert.deepEqual(original.layout, defaultComposerLayout());
  const f = preferenceFixture();
  f.store.update({ display: { model: 'hidden', context: 'both' } });
  f.store.moveTool('model', 'bottomRight', 99);
  assert.deepEqual(f.store.value.layout.bottomRight, ['model']);
  assert.equal(f.store.value.display.model, 'text');
  assert.equal(f.store.value.display.context, 'both');
  f.store.moveTool('context', 'bottomRight', -1);
  assert.deepEqual(f.store.value.layout.bottomRight, ['context', 'model']);
  f.store.moveTool('context', 'bottomRight', 99);
  assert.deepEqual(f.store.value.layout.bottomRight, ['model', 'context']);
  f.store.moveTool('context', 'more', 0);
  assert.equal(f.store.value.display.context, 'both');
  assert.deepEqual(new ComposerPreferences(f.storage).value, f.store.value);
  const before = f.store.value,
    writes = f.writes();
  for (const [id, zone] of [
    ['more', 'more'],
    ['submit', 'topLeft'],
    ['unknown', 'topLeft'],
    ['model', 'unknown'],
  ])
    f.store.moveTool(id, zone, 0);
  assert.equal(f.store.value, before);
  assert.equal(f.writes(), writes);
});
test('submit display is customizable while its position stays outside movable tools', () => {
  for (const mode of ['icon', 'text', 'both'])
    assert.equal(normalizeComposerDisplay({ submit: mode }).submit, mode);
  assert.equal(normalizeComposerDisplay({ submit: 'invalid' }).submit, 'both');
  const layout = defaultComposerLayout();
  layout.topLeft.push('submit');
  layout.more.push('submit');
  assert.deepEqual(normalizeComposerLayout(layout), defaultComposerLayout());
  const f = preferenceFixture();
  f.store.update({ display: { submit: 'icon' } });
  assert.equal(new ComposerPreferences(f.storage).value.display.submit, 'icon');
  f.store.resetLayout();
  assert.equal(f.store.value.display.submit, 'both');
});

test('a current native stop allowance survives a concurrent write without enabling another send',()=>{
 const action=deriveComposerAction({running:true,hasDraft:false,pending:true,stopEnabled:true,sendEnabled:false});
 assert.equal(action.stop,true);assert.equal(action.stopDisabled,false);assert.equal(action.sendDisabled,true);
 assert.equal(deriveComposerAction({running:true,hasDraft:false,pending:true,stopEnabled:false}).stopDisabled,true);
});


test('legacy layouts keep their order while quota follows model until explicitly customized',()=>{
 const old={topLeft:['permissions','model'],topRight:['context','collapse'],bottomLeft:['attachments','more'],bottomRight:[],more:['skills','sendMode','plan','goal']};
 const migrated=normalizeComposerLayout(old);assert.deepEqual(migrated.topLeft,['permissions','model','quota']);
 for(const zone of composerZones)assert.deepEqual(migrated[zone].filter(id=>id!=='quota'),old[zone]);
 const moved=moveComposerTool({layout:migrated,display:{}},'quota','bottomRight',0);assert.deepEqual(normalizeComposerLayout(moved.layout).bottomRight,['quota']);
 const hidden=normalizeComposerPreferences({...moved,display:{quota:'hidden'}});assert.ok(hidden.layout.more.includes('quota'));assert.equal(hidden.display.quota,'hidden');
});
