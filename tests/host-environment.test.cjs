'use strict';
const test = require('node:test'), assert = require('node:assert/strict');
const fs = require('node:fs'), path = require('node:path'), vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../web/hosts/environment.js'), 'utf8');

function fixture(userAgent = 'Mozilla/5.0') {
  const classes = new Set();
  const context = vm.createContext({
    navigator:{userAgent},
    document:{documentElement:{classList:{contains:name => classes.has(name)}}}
  });
  vm.runInContext(source, context);
  return {host:vm.runInContext('BridgeHost', context), context, classes};
}

test('native identity preserves the existing user-agent token grammar', () => {
  const {host} = fixture();
  for (const [ua, kind] of [
    ['Mozilla/5.0 BridgeMobile/0.1-iOS', 'ios'],
    ['Mozilla/5.0 BridgeMobile/0.1-Android', 'android'],
    ['BridgeMobile/2.0.0-preview.3-iOS Safari/605.1.15', 'ios'],
    ['BridgeMobile/build_42-Android\tOther/1', 'android'],
    ['prefixBridgeMobile/0.1-iOS', 'ios'],
    ['BridgeMobile/-iOS', 'browser'],
    ['BridgeMobile/0.1-ios', 'browser'],
    ['bridgemobile/0.1-iOS', 'browser'],
    ['BridgeMobile/0.1-AndroidExtra', 'browser'],
    ['BridgeMobile/0.1-iOS;', 'browser'],
    ['BridgeMobile/0.1-iOS/', 'browser'],
    ['BridgeMobile/0.1-Windows', 'browser'],
    ['Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X)', 'browser'],
    ['Mozilla/5.0 (Linux; Android 15)', 'browser'],
    ['', 'browser']
  ]) {
    assert.equal(host.kind(ua), kind, ua);
    assert.equal(host.isNativeApp(ua), kind !== 'browser', ua);
  }
});

test('default identity reads the current user agent while explicit arguments override it', () => {
  const {host, context} = fixture('BridgeMobile/0.1-iOS');
  assert.equal(host.kind(), 'ios');
  assert.equal(host.isNativeApp(), true);
  assert.equal(host.kind('Mozilla/5.0'), 'browser');
  assert.equal(host.isNativeApp('Mozilla/5.0'), false);
  context.navigator.userAgent = 'BridgeMobile/0.1-Android';
  assert.equal(host.kind(), 'android');
});

test('late native appearance injection is observed independently of native identity', () => {
  const {host, classes} = fixture('BridgeMobile/0.1-iOS');
  assert.equal(host.isNativeApp(), true);
  assert.equal(host.hasNativeLayout(), false);
  classes.add('bridge-mobile');
  assert.equal(host.hasNativeLayout(), true);
  assert.equal(host.kind(), 'ios');
  classes.delete('bridge-mobile');
  assert.equal(host.hasNativeLayout(), false);
});

test('native appearance never changes a browser into a native host', () => {
  const {host, classes} = fixture();
  classes.add('bridge-mobile');
  assert.equal(host.hasNativeLayout(), true);
  assert.equal(host.kind(), 'browser');
  assert.equal(host.isNativeApp(), false);
});

test('host identity and native appearance do not depend on viewport or pointer policy', () => {
  for (const [ua, kind] of [['Mozilla/5.0', 'browser'], ['BridgeMobile/0.1-iOS', 'ios'], ['BridgeMobile/0.1-Android', 'android']]) {
    const {host, context, classes} = fixture(ua);
    context.matchMedia = () => { throw Error('host detection must not query layout'); };
    for (const width of [360, 720, 721, 1440]) {
      context.innerWidth = width;
      context.navigator.maxTouchPoints = width <= 720 ? 5 : 0;
      assert.equal(host.kind(), kind);
      assert.equal(host.isNativeApp(), kind !== 'browser');
      assert.equal(host.hasNativeLayout(), false);
      classes.add('bridge-mobile');
      assert.equal(host.hasNativeLayout(), true);
      classes.clear();
    }
  }
});

test('spoofable host hints expose no native authorization or privileged bridge', () => {
  const {host, context, classes} = fixture('BridgeMobile/0.1-Android');
  for (const name of ['location', 'prompt', 'fetch', 'bridgeToken']) {
    Object.defineProperty(context, name, {get() { throw Error(`host must not use ${name}`); }});
  }
  Object.defineProperty(context.navigator, 'clipboard', {get() { throw Error('host must not use clipboard'); }});
  classes.add('bridge-mobile');
  assert.equal(host.kind(), 'android');
  assert.equal(host.isNativeApp(), true);
  assert.equal(host.hasNativeLayout(), true);
  assert.deepEqual(Object.keys(host).sort(), ['hasNativeLayout', 'isNativeApp', 'kind']);
  assert.equal(Object.isFrozen(host), true);
});

test('the CommonJS entry parses explicit user agents without a browser environment', () => {
  const context = vm.createContext({module:{exports:{}}});
  vm.runInContext(source, context);
  const host = context.module.exports;
  assert.equal(host.kind(), 'browser');
  assert.equal(host.kind('BridgeMobile/0.1-iOS'), 'ios');
  assert.equal(host.isNativeApp('BridgeMobile/0.1-Android'), true);
  assert.equal(Object.isFrozen(host), true);
});
