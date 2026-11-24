'use strict';
const test=require('node:test'),assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path');

const include=fs.readFileSync(path.join(__dirname,'../desktop/installer.nsh'),'utf8');

test('optional uninstaller cleanup targets only the gateway user-data directory',()=>{
  assert.match(include,/Section \/o "un\.清理网关设置与缓存"/);
  assert.match(include,/\$APPDATA\\codex-mobile-bridge-desktop/);
  assert.match(include,/\$\{ifNot\} \$\{isUpdated\}/);
  assert.match(include,/GetFileAttributesW/);
  assert.match(include,/0x400/);
  assert.match(include,/Function un\.CmbCheckDataDirectory/);
  assert.match(include,/\$R2 == "claude-accounts"/);
  assert.match(include,/Call un\.CmbCheckDataDirectory/);
  assert.match(include,/\$CmbDataCleanupSafe == 1/);
  const removals=[...include.matchAll(/RMDir \/r "([^"]+)"/g)].map(match=>match[1]);
  assert.deepEqual(removals,['$0']);
  assert.doesNotMatch(include,/\$APPDATA\\Codex(?:\\|"|\s)/i);
  assert.match(include,/Codex 和其他 Agent 的账号、会话及配置不会删除/);
});
