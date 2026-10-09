'use strict';
const test=require('node:test'),assert=require('node:assert/strict');
const i18n=require('../web/i18n.js'),desktop=require('../desktop/i18n.js');
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


test('close choices and exit failures translate without implying that disabling ends tasks',()=>{
 const messages=["当前 Claude Desktop 不支持后台退出；请在电脑上从 Claude 菜单或系统托盘选择“退出”，也可以选择“仅停用手机接入”","Harness 接入需要更新才能正常退出；请先在电脑端退出 Harness，再重新连接","Harness 尚未退出，请在电脑端处理退出提示后重试；尚未强制结束进程","无法确认客户端所有任务均已结束；可选择“仅停用手机接入”保留电脑 App，或在电脑端退出后重试","客户端尚未连接，无法确认任务状态；可选择“仅停用手机接入”，或在电脑端退出 App 后重试","正在停用接入…"];
 i18n.setLanguage('en');for(const source of messages){assert.doesNotMatch(i18n.t(source),/[\u4e00-\u9fff]/,source);assert.equal(desktop.translate(source,'en'),i18n.t(source));}
 assert.match(i18n.t('保留电脑 App 和现有任务。'),/tasks running/);i18n.setLanguage('zh');
});
