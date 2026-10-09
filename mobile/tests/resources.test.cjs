'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const root = path.resolve(__dirname, '../..');
const read = value => fs.readFileSync(path.join(root, value), 'utf8');
const shared = ['mobile-ui.js', 'mobile-clipboard.js'];
const android = [...shared, 'mobile-session.js'];

function xcodeObjects() {
  const source = read('mobile/ios/BridgePreview.xcodeproj/project.pbxproj');
  return new Map([...source.matchAll(/([A-F0-9]{24})(?: \/\*[^*]*\*\/)? = \{([\s\S]*?)\};/g)]
    .map(([, id, body]) => [id, body.replace(/\/\*[\s\S]*?\*\//g, '')]));
}
const field = (body, name) => body.match(new RegExp(`\\b${name} = ([^;]+);`))?.[1].trim().replace(/^"|"$/g, '');
const ids = value => value.match(/[A-F0-9]{24}/g) || [];

test('iOS target packages each shared script exactly once and compiles its resource loader', () => {
  const objects = xcodeObjects();
  const target = [...objects.values()].find(body => field(body, 'isa') === 'PBXNativeTarget' && field(body, 'name') === 'BridgePreview');
  assert.ok(target);
  const phases = ids(field(target, 'buildPhases')).map(id => objects.get(id));
  function files(kind) {
    const phase = phases.find(body => field(body, 'isa') === kind);
    assert.ok(phase, kind);
    return ids(field(phase, 'files')).map(id => objects.get(field(objects.get(id), 'fileRef')));
  }
  const resources = files('PBXResourcesBuildPhase');
  const scripts = resources.filter(body => field(body, 'path').endsWith('.js'));
  assert.equal(scripts.length, shared.length);
  for (const name of shared) {
    const matches = scripts.filter(body => field(body, 'path') === '../shared/web/' + name);
    assert.equal(matches.length, 1, name);
    assert.equal(field(matches[0], 'sourceTree'), 'SOURCE_ROOT');
    assert.equal(fs.readFileSync(path.resolve(root, 'mobile/ios', field(matches[0], 'path')), 'utf8'), read('mobile/shared/web/' + name));
  }
  assert.equal(files('PBXSourcesBuildPhase').filter(body => field(body, 'path') === 'WebScripts.swift').length, 1);
});

test('ordinary APK and push Gradle package the same scripts without overlapping filenames', () => {
  const gradle = read('mobile/android-push/build.gradle');
  const gradleDirs = [...gradle.match(/assets\.srcDirs = \[([^\]]+)\]/)[1].matchAll(/'([^']+)'/g)]
    .map(([, value]) => path.resolve(root, 'mobile/android-push', value));
  const builder = read('scripts/build-mobile-android.py');
  // Read only the asset inputs; never import a build script with compiler side effects.
  const sources = builder.match(/for directory in \(([^\n]+)\):/);
  assert.ok(sources);
  assert.match(builder, /archive\.write\(asset,'assets\/'\+asset\.name\)/);
  const buildDirs = [...sources[1].matchAll(/(ROOT|source)\/'([^']+)'/g)]
    .map(([, base, value]) => path.resolve(root, base === 'ROOT' ? '' : 'mobile/android', value));
  assert.deepEqual(buildDirs, gradleDirs);
  for (const directories of [buildDirs, gradleDirs]) {
    const names = directories.flatMap(directory => fs.readdirSync(directory).filter(name => name.endsWith('.js')));
    assert.deepEqual(names.slice().sort(), android.slice().sort());
    assert.equal(new Set(names).size, names.length);
  }
  const loader = read('mobile/android/src/io/github/try2love/codexbridge/MobileWebScripts.java');
  assert.deepEqual([...loader.matchAll(/"(mobile-[^"]+\.js)"/g)].map(([, name]) => name), android);
  assert.match(loader, /throw new IllegalStateException\("Missing mobile layout",e\)/);
});

test('iOS missing resources stop connection visibly before replacing the current page', () => {
  const app = read('mobile/ios/BridgePreview/App.swift');
  const loader = read('mobile/ios/BridgePreview/WebScripts.swift');
  assert.match(loader, /init\(bundle: Bundle = \.main\) throws/);
  assert.match(loader, /guard let url = bundle\.url\(forResource: name, withExtension: "js"\) else \{\s+throw NSError/);
  assert.match(loader, /return try String\(contentsOf: url, encoding: \.utf8\)/);
  assert.doesNotMatch(loader, /try\?|fatalError|preconditionFailure/);
  const connect = app.slice(app.indexOf('private func connect('), app.indexOf('@objc private func refresh()'));
  assert.match(connect, /do \{ scripts = try WebScripts\(\) \}\s+catch \{ info\(error\.localizedDescription\); return \}/);
  assert.ok(connect.indexOf('try WebScripts()') < connect.indexOf('clear()'));
  assert.equal([...connect.matchAll(/injectionTime: \.atDocumentEnd, forMainFrameOnly: true/g)].length, 3);
  assert.match(connect, /scripts\.appearance\.replacingOccurrences\(of: "__BRIDGE_CLIPBOARD_TOKEN__", with: clipboardToken\)/);
  assert.match(connect, /scripts\.clipboard\.replacingOccurrences\(of: "__BRIDGE_CLIPBOARD_TOKEN__", with: clipboardToken\)/);
  assert.doesNotMatch(app, /private static let web(Appearance|Clipboard) =/);
});
