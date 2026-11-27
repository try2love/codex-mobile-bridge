'use strict';

const composerTools = Object.freeze(
  [
    { id: 'more', label: '工具面板', zone: 'topLeft' },
    { id: 'skills', label: 'Skill', zone: 'more', addMenu: true },
    { id: 'permissions', label: '权限', zone: 'topLeft' },
    { id: 'context', label: '上下文使用量', shortLabel: '上下文', zone: 'topRight' },
    { id: 'collapse', label: '收起输入区', zone: 'topRight' },
    { id: 'attachments', label: '添加文件或图片', shortLabel: '附件', zone: 'bottomLeft' },
    {
      id: 'model',
      label: '模型与思考强度',
      shortLabel: '模型·强度',
      zone: 'bottomLeft',
      display: 'text',
    },
    { id: 'quota', label: '剩余额度', zone: 'bottomLeft', display: 'both' },
    { id: 'sendMode', label: '发送方式', zone: 'more', display: 'text' },
    { id: 'plan', label: '计划模式', zone: 'more' },
    { id: 'goal', label: '目标模式', zone: 'more' },
  ].map(Object.freeze),
);
const composerSubmitTool = Object.freeze({ id: 'submit', label: '发送按钮', display: 'both' });
const composerToolIds = Object.freeze(composerTools.map((tool) => tool.id));
const composerZones = Object.freeze([
  'topLeft',
  'topRight',
  'bottomLeft',
  'bottomRight',
  'more',
]);
const composerPreferencesKey = 'bridge-composer-preferences';
const composerPreferenceModes =
  typeof module !== 'undefined'
    ? require('./composer-state.js').composerFollowUpModes
    : composerFollowUpModes;

function defaultComposerLayout() {
  return Object.fromEntries(
    composerZones.map((zone) => [
      zone,
      composerTools.filter((tool) => tool.zone === zone).map((tool) => tool.id),
    ]),
  );
}
function composerPreset(id) {
  const layout = defaultComposerLayout();
  const display = normalizeComposerDisplay();
  if (id === 'single') {
    layout.topLeft = [];
    layout.topRight = [];
    layout.bottomLeft = ['more', 'model', 'quota'];
    layout.bottomRight = ['context', 'attachments'];
    layout.more = ['permissions', 'collapse', 'skills', 'sendMode', 'plan', 'goal'];
    display.collapse = 'hidden';
  } else if (id !== 'default') {
    throw new Error('Unknown composer preset');
  }
  return { layout, display };
}
function normalizeComposerLayout(value) {
  if (!value || !composerZones.every((zone) => Array.isArray(value[zone]))) {
    return defaultComposerLayout();
  }
  const seen = new Set();
  const layout = Object.fromEntries(
    composerZones.map((zone) => [
      zone,
      value[zone].filter((id) => {
        if (
          !composerToolIds.includes(id) ||
          seen.has(id) ||
          (id === 'more' && zone === 'more')
        ) {
          return false;
        }
        seen.add(id);
        return true;
      }),
    ]),
  );
  for (const id of composerToolIds) {
    if (!seen.has(id)) {
      if (id === 'more') {
        layout.topLeft.unshift(id);
      } else if (id === 'quota') {
        const zone = composerZones.find((key) => layout[key].includes('model')) || 'bottomLeft';
        layout[zone].splice(layout[zone].indexOf('model') + 1, 0, id);
      } else {
        layout.more.push(id);
      }
    }
  }
  return layout;
}
function normalizeComposerDisplay(value) {
  return Object.fromEntries(
    [...composerTools, composerSubmitTool].map((tool) => {
      const mode = value?.[tool.id];
      const isVisibleMode = ['icon', 'text', 'both'].includes(mode);
      const canHide = !['more', 'submit'].includes(tool.id);
      const isValidMode = isVisibleMode || (mode === 'hidden' && canHide);
      return [tool.id, isValidMode ? mode : tool.display || 'icon'];
    }),
  );
}

function normalizeComposerPreferences(value) {
  const layout = normalizeComposerLayout(value?.layout);
  const display = normalizeComposerDisplay(value?.display);
  for (const id of composerToolIds) {
    if (display[id] === 'hidden') {
      for (const zone of composerZones) {
        if (zone !== 'more') {
          layout[zone] = layout[zone].filter((tool) => tool !== id);
        }
      }
      if (!layout.more.includes(id)) {
        layout.more.push(id);
      }
    }
  }
  for (const zone of composerZones) {
    Object.freeze(layout[zone]);
  }
  return Object.freeze({
    layout: Object.freeze(layout),
    display: Object.freeze(display),
    followUpMode: composerPreferenceModes.includes(value?.followUpMode)
      ? value.followUpMode
      : 'queue',
  });
}

function moveComposerTool(value, id, zone, index) {
  if (
    !composerToolIds.includes(id) ||
    !composerZones.includes(zone) ||
    (id === 'more' && zone === 'more')
  ) {
    return value;
  }
  const layout = normalizeComposerLayout(value.layout);
  const display = normalizeComposerDisplay(value.display);
  for (const key of composerZones) {
    layout[key] = layout[key].filter((tool) => tool !== id);
  }
  layout[zone].splice(Math.max(0, Math.min(index, layout[zone].length)), 0, id);
  if (zone !== 'more' && display[id] === 'hidden') {
    display[id] = normalizeComposerDisplay()[id];
  }
  return { ...value, layout, display };
}

class ComposerPreferences {
  static shared() {
    if (!this.instance) {
      let storage;
      try {
        storage = window.localStorage;
      } catch {
        // Some WebViews reject access to localStorage before getItem can run.
      }
      this.instance = new ComposerPreferences(storage, window);
    }
    return this.instance;
  }
  constructor(storage, events) {
    this.storage = storage;
    this.listeners = new Set();
    this.value = this.read();
    this.onStorage = (event) => {
      if (event.key === composerPreferencesKey || event.key === null) {
        const next = this.read();
        this.publish(next);
      }
    };
    this.events = events;
    events?.addEventListener('storage', this.onStorage);
    this.onRestore = () => { this.publish(this.read()); this.restoreOfflinePreferences(); };
    events?.addEventListener('bridge-composer-preferences-restored', this.onRestore);
    this.onAuthenticated = () => this.restoreOfflinePreferences();
    events?.document?.addEventListener('bridge-authenticated', this.onAuthenticated);
  }
  restoreOfflinePreferences() {
    const feature = this.events?.BridgeOfflineComposer, before = this.value;
    if (!feature) return;
    void feature.preferences().then(value => {
      if (this.value !== before) return;
      if (value?.layout) {
        const next = normalizeComposerPreferences(value);
        try { this.storage?.setItem(composerPreferencesKey, JSON.stringify(next)); } catch {}
        this.publish(next);
      } else if (this.storage?.getItem(composerPreferencesKey)) {
        return this.saveOfflinePreferences(before);
      }
    }).catch(() => {});
  }
  saveOfflinePreferences(value) {
    if (this.events?.OfflineChatReader?.composerWritable === false) return;
    return this.events?.BridgeOfflineComposer?.savePreferences(value);
  }
  read() {
    try {
      return normalizeComposerPreferences(
        JSON.parse(this.storage?.getItem(composerPreferencesKey)),
      );
    } catch {
      return normalizeComposerPreferences();
    }
  }
  subscribe(listener) {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }
  publish(next) {
    const previous = this.value;
    const layout =
      JSON.stringify(next.layout) === JSON.stringify(previous.layout)
        ? previous.layout
        : next.layout;
    const display =
      JSON.stringify(next.display) === JSON.stringify(previous.display)
        ? previous.display
        : next.display;
    if (
      layout === previous.layout &&
      display === previous.display &&
      next.followUpMode === previous.followUpMode
    )
      return;
    this.value = Object.freeze({ ...next, layout, display });
    for (const listener of this.listeners) listener(this.value);
  }
  update(patch) {
    const next = normalizeComposerPreferences({ ...this.value, ...patch });
    const serialized = JSON.stringify(next);
    if (serialized === JSON.stringify(this.value)) {
      return;
    }
    try {
      if (!this.storage) {
        throw Error();
      }
      this.storage.setItem(composerPreferencesKey, serialized);
    } catch {
      throw new Error('工具栏设置保存失败，请检查浏览器存储权限。');
    }
    // Publish committed settings together across all open composers.
    this.publish(next);
    void this.saveOfflinePreferences(next)?.catch(() => {});
    if (globalThis.CustomEvent) this.events?.dispatchEvent?.(new CustomEvent('bridge-composer-preferences-change', {detail: next}));
  }
  setDisplay(id, mode) {
    this.update({ display: { ...this.value.display, [id]: mode } });
  }
  moveTool(id, zone, index) {
    this.update(moveComposerTool(this.value, id, zone, index));
  }
  applyPreset(id) {
    this.update(composerPreset(id));
  }
  resetLayout() {
    this.applyPreset('default');
  }
  dispose() {
    this.events?.removeEventListener('storage', this.onStorage);
    this.events?.removeEventListener('bridge-composer-preferences-restored', this.onRestore);
    this.events?.document?.removeEventListener('bridge-authenticated', this.onAuthenticated);
    this.listeners.clear();
  }
}
if (typeof module !== 'undefined') {
  module.exports = {
    composerTools,
    composerToolIds,
    composerZones,
    composerPreferencesKey,
    composerPreset,
    defaultComposerLayout,
    normalizeComposerLayout,
    normalizeComposerDisplay,
    normalizeComposerPreferences,
    moveComposerTool,
    ComposerPreferences,
  };
}
