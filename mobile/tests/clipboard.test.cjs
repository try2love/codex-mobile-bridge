const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const root = path.resolve(__dirname, '../..');
const script = fs.readFileSync(path.join(root, 'mobile/android/assets/mobile-clipboard.js'), 'utf8');
function fixture({ reply = 'copied', late = false, frame = false } = {}) {
  const events = {}, writes = [], errors = [], timers = [];
  const context = { navigator: {}, setTimeout: fn => timers.push(fn),
    CustomEvent: class { constructor(type, init) { this.type = type; this.detail = init.detail; } },
    document: { addEventListener: (name, fn) => { events[name] = fn; }, dispatchEvent: e => errors.push(e.detail) },
    prompt: (command, text) => { writes.push({ command, text }); return reply; }
  };
  context.window = context; context.top = frame ? {} : context;
  vm.createContext(context);
  const loadWeb = () => vm.runInContext(fs.readFileSync(path.join(root, 'web/message-actions.js'), 'utf8'), context);
  if (!late) loadWeb();
  vm.runInContext(script.replace('__BRIDGE_CLIPBOARD_TOKEN__', 'test-token'), context);
  if (late) { loadWeb(); events.DOMContentLoaded(); }
  const button = { disabled: false, textContent: '复制' };
  const click = (trusted = true) => events.click({ isTrusted: trusted, target: { closest: () => button } });
  return { context, events, button, click, writes, errors, timers };
}
test('copies async full message exactly without browser clipboard or manual dialog', async () => {
  const f = fixture(); f.click();
  const text = '中文🙂\n```js\nconst value = "quotes\\slashes";\n```\n';
  await f.context.BridgeClipboard.copy(async () => text, f.button);
  assert.deepEqual(f.writes, [{ command: 'codexbridge-copy:test-token', text }]);
  assert.equal(f.button.textContent, '已复制'); assert.equal(f.button.disabled, false);
  assert.deepEqual(f.errors, []); f.timers[0](); assert.equal(f.button.textContent, '复制');
});
test('code-copy path uses the same async writer and handles deferred page scripts', async () => {
  const f = fixture({ late: true }); f.button.textContent = '复制代码'; f.click();
  await f.context.BridgeClipboard.copy(async () => 'a\n b\t', f.button);
  assert.equal(f.writes[0].text, 'a\n b\t'); assert.equal(f.button.textContent, '已复制');
});
test('native rejection does not report copied or open the manual-copy dialog', async () => {
  const f = fixture({ reply: null }); f.click();
  await f.context.BridgeClipboard.copy(() => 'text', f.button);
  assert.equal(f.button.textContent, '复制'); assert.equal(f.button.disabled, false);
  assert.deepEqual(f.errors, ['复制失败，请重试']); assert.equal(f.timers.length, 0);
});
test('no write without a real click, and a click only authorizes one copy', async () => {
  const f = fixture();
  await f.context.BridgeClipboard.copy(() => 'auto', f.button);
  f.click(false); await f.context.BridgeClipboard.copy(() => 'synthetic', f.button);
  assert.equal(f.writes.length, 0);
  f.click(); await f.context.BridgeClipboard.copy(() => 'user', f.button);
  await f.context.BridgeClipboard.copy(() => 'again', f.button);
  assert.equal(f.writes.length, 1);
});
test('full-text fetch failure restores the button without copying partial text', async () => {
  const f = fixture(); f.click();
  await f.context.BridgeClipboard.copy(async () => { throw new Error('读取失败'); }, f.button);
  assert.deepEqual(f.errors, ['读取失败']); assert.equal(f.writes.length, 0);
  assert.equal(f.button.disabled, false); assert.equal(f.button.textContent, '复制');
});
test('oversized text is rejected, never silently truncated', async () => {
  const f = fixture(); f.click();
  await f.context.BridgeClipboard.copy(() => 'x'.repeat(262145), f.button);
  assert.equal(f.writes.length, 0); assert.deepEqual(f.errors, ['内容过长，请分段复制']);
});
test('embedded frames cannot install the native copy hook', () => {
  const f = fixture({ frame: true }); assert.equal(f.events.click, undefined);
});
test('iOS and Android ship identical clipboard behavior', () => {
  const swift = fs.readFileSync(path.join(root, 'mobile/ios/BridgePreview/App.swift'), 'utf8');
  const embedded = swift.split('private static let webClipboard = """')[1].split('"""')[0];
  assert.equal(embedded.trim().split('\n').map(l => l.replace(/^    /, '')).join('\n').trim(), script.trim());
});
