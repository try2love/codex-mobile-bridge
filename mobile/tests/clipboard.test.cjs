const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const root = path.resolve(__dirname, '../..');
const script = fs.readFileSync(path.join(root, 'mobile/shared/web/mobile-clipboard.js'), 'utf8');
const html = fs.readFileSync(path.join(root, 'web/index.html'), 'utf8');
function fixture({ reply = 'copied', late = false, frame = false, domReadyBeforeNative = false } = {}) {
  const events = {}, listeners = {}, writes = [], errors = [], timers = [];
  const context = { navigator: {}, setTimeout: fn => timers.push(fn),
    CustomEvent: class { constructor(type, init) { this.type = type; this.detail = init.detail; } },
    document: { readyState: 'loading', addEventListener: (name, fn) => { events[name] = fn; listeners[name] = (listeners[name] || 0) + 1; }, dispatchEvent: e => errors.push(e.detail) },
    prompt: (command, text) => { writes.push({ command, text }); return reply; }
  };
  context.window = context; context.top = frame ? {} : context;
  vm.createContext(context);
  const loadWeb = () => vm.runInContext(fs.readFileSync(path.join(root, 'web/features/chat/message-actions.js'), 'utf8'), context);
  if (!late) loadWeb();
  if (domReadyBeforeNative) { context.document.readyState = 'complete'; events.DOMContentLoaded?.(); }
  vm.runInContext(script.replace('__BRIDGE_CLIPBOARD_TOKEN__', 'test-token'), context);
  if (late) { loadWeb(); events.DOMContentLoaded(); }
  const button = { disabled: false, textContent: '复制' };
  const click = (trusted = true) => events.click({ isTrusted: trusted, target: { closest: () => button } });
  return { context, events, listeners, button, click, writes, errors, timers };
}
test('repeated native injection retains one set of handlers and one-click authorization', async () => {
  const f = fixture(); f.click();
  vm.runInContext(script.replaceAll('__BRIDGE_CLIPBOARD_TOKEN__', 'test-token'), f.context);
  assert.deepEqual(f.listeners, { 'bridge-language': 1, click: 1, DOMContentLoaded: 1 });
  await f.context.BridgeClipboard.copy(() => 'once', f.button);
  await f.context.BridgeClipboard.copy(() => 'twice', f.button);
  assert.equal(f.writes.length, 1);
});
test('language sync skips unchanged values and retries a rejected native save', () => {
  let language = 'zh', accepted = false;
  const events = {}, writes = [], context = { BridgeI18n: { language: () => language },
    prompt: (command, value) => { writes.push(value); return accepted ? 'saved' : null; },
    document: { addEventListener: (name, fn) => events[name] = fn } };
  context.window = context; context.top = context;
  vm.createContext(context);
  vm.runInContext(script.replace("'__BRIDGE_NATIVE_LANGUAGE__'", "'zh'"), context);
  assert.deepEqual(writes, []);
  events['bridge-language'](); assert.deepEqual(writes, []);
  language = 'en'; events['bridge-language'](); accepted = true; events['bridge-language'](); events['bridge-language']();
  assert.deepEqual(writes, ['en', 'en']);
  language = 'zh'; events['bridge-language'](); events['bridge-language']();
  assert.deepEqual(writes, ['en', 'en', 'zh']);
});
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
test('published native shells can install after DOMContentLoaded with the eager clipboard definition', async () => {
  assert.match(html, /<script src="\/message-actions\.js\?[^\"]+" defer id="bridge-clipboard-script"><\/script>/);
  assert.doesNotMatch(html, /<script data-bridge-src="\/message-actions\.js/);
  const eager = [...html.matchAll(/<script src="([^"]+)" defer id="bridge-clipboard-script"><\/script>/g)].map(match => match[1]);
  assert.deepEqual(eager.map(url => url.split('?')[0]), ['/message-actions.js']);
  const f = fixture({ domReadyBeforeNative: true });
  f.click();
  await f.context.BridgeClipboard.copy(() => 'late native copy', f.button);
  assert.equal(f.writes.at(-1).text, 'late native copy');
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
test('both native hosts load the shared clipboard resource', () => {
  const swift = fs.readFileSync(path.join(root, 'mobile/ios/BridgePreview/WebScripts.swift'), 'utf8');
  const java = fs.readFileSync(path.join(root, 'mobile/android/src/io/github/try2love/codexbridge/MobileWebScripts.java'), 'utf8');
  assert.match(swift, /clipboard = try Self\.source\("mobile-clipboard", bundle: bundle\)/);
  assert.match(java, /"mobile-clipboard\.js"/);
  assert.equal(fs.existsSync(path.join(root, 'mobile/android/assets/mobile-clipboard.js')), false);
});

test('native settings own the chat language without saving a resolved system choice as an override', () => {
  let language='zh';
  const events={},writes=[];
  const field={value:language,onchange(){language=this.value;events['bridge-language']();}};
  const context={BridgeI18n:{language:()=>language},document:{addEventListener:(name,fn)=>{events[name]=fn;},getElementById:()=>field},
    prompt:(_command,value)=>{writes.push(value);return 'saved';}};
  context.window=context;context.top=context;
  vm.runInNewContext(script.replace("'__BRIDGE_NATIVE_LANGUAGE__'","'en'"),context);
  assert.equal(language,'en');assert.deepEqual(writes,[]);
  context.BridgeNativeLanguage.apply('zh');
  assert.equal(language,'zh');assert.deepEqual(writes,[]);
  field.value='en';field.onchange();
  assert.deepEqual(writes,['en']);assert.equal(context.BridgeNativeLanguage.value,'en');
});
