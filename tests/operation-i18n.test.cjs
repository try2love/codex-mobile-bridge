'use strict';
const test=require('node:test'),assert=require('node:assert/strict');
const i18n=require('../web/shared/i18n.js'),desktop=require('../desktop/shared/i18n.js');
test('English operation failures and dynamic channel feedback are translated',()=>{
 i18n.setLanguage('en');
 for(const source of ['请先保存连接配置。','账号不存在，请刷新列表','账号身份已变化','找不到桌面 App 的 Codex 运行时','保存的账号需要重新登录','PushPlus 测试失败，请检查服务地址、认证和网络','部分通道发送失败：ntfy, pushplus；请在手机确认其他通道是否收到。']){
  assert.doesNotMatch(i18n.t(source),/[\u4e00-\u9fff]/);assert.doesNotMatch(i18n.t("Error invoking remote method 'bridge:accounts': Error: "+source),/[\u4e00-\u9fff]/);assert.doesNotMatch(desktop.translate("Error invoking remote method 'bridge:accounts': Error: "+source,'en'),/[\u4e00-\u9fff]/);
 }
 assert.equal(i18n.t('用户的中文聊天标题'),'用户的中文聊天标题');
 i18n.setLanguage('zh');assert.equal(i18n.t('账号身份已变化'),'账号身份已变化');
});
test('shared relay preview entry is not mounted in the desktop UI',()=>{
 const fs=require('node:fs'),path=require('node:path');const html=fs.readFileSync(path.join(__dirname,'../desktop/index.html'),'utf8');
 assert.doesNotMatch(html,/<script[^>]+shared-relay\.js/);
});
