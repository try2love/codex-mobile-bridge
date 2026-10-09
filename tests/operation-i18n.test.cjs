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

test('background force quit choices, risks and progress translate for desktop and mobile',()=>{
 const messages=['后台强制结束','结束应用进程，可能丢失未保存内容或中断任务。锁屏时也可使用。','正在后台强制结束…','重试后台强制结束'];
 i18n.setLanguage('en');for(const source of messages){assert.doesNotMatch(i18n.t(source),/[\u4e00-\u9fff]/,source);assert.equal(desktop.translate(source,'en'),i18n.t(source));}i18n.setLanguage('zh');
});


test('close choices and exit failures translate without implying that disabling ends tasks',()=>{
 const messages=["当前 Claude Desktop 不支持后台退出；请在电脑上从 Claude 菜单或系统托盘选择“退出”，也可以选择“仅停用手机接入”","Harness 接入需要更新才能正常退出；请先在电脑端退出 Harness，再重新连接","Harness 尚未退出，请在电脑端处理退出提示后重试；尚未强制结束进程","无法确认客户端所有任务均已结束；可选择“仅停用手机接入”保留电脑 App，或在电脑端退出后重试","客户端尚未连接，无法确认任务状态；可选择“仅停用手机接入”，或在电脑端退出 App 后重试","正在停用接入…"];
 i18n.setLanguage('en');for(const source of messages){assert.doesNotMatch(i18n.t(source),/[\u4e00-\u9fff]/,source);assert.equal(desktop.translate(source,'en'),i18n.t(source));}
 assert.match(i18n.t('保留电脑 App 和现有任务。'),/tasks running/);i18n.setLanguage('zh');
});


test('native app quit prompts and results translate in both desktop and mobile',()=>{
 const messages=["通过 Claude 原生菜单退出，菜单可能短暂出现；如有任务或保存确认，请在电脑端处理。","安装和登录请在电脑端完成。关闭接入时可选择保留或退出电脑 App。Claude 退出会短暂打开原生菜单。Claude 首次连接需在电脑端手动初始化。","此退出操作仅用于 Claude 桌面端","Claude 进程已变化，请重新检查后再退出","请在电脑端处理 Claude 的任务或保存提示后重试","Claude 尚未退出，请在电脑端处理任务或保存提示后重试","已取消 Claude 退出","找不到 Claude 的原生退出菜单，请在电脑端检查菜单后重试","Claude 退出菜单不唯一，请在电脑端检查后重试","Claude 主进程已改变，退出请求已停止","焦点已离开 Claude，退出请求已停止，请重试","Claude 有待处理的原生对话框，请在电脑端确认或取消","Claude 已正常退出","Claude 正在等待电脑端退出确认，请自行确认或取消","已请求 Claude 正常退出，正在等待保存和退出完成","缺少 Claude 原生退出组件，请重新构建或安装网关 App","无法启动 Claude 原生退出组件，请检查网关安装","Claude 原生退出请求超时，请在电脑端检查退出状态","Claude 原生退出组件未返回有效状态，请在电脑端检查退出状态","此退出操作仅用于 Harness 桌面端","当前 Harness 未提供可验证的后台退出通道，请更新桌面端后重试","Harness 运行实例的数据目录无法核对，请重新扫描后重试","Harness 正常退出请求尚未完成，请稍后重试","Harness 拒绝了正常退出请求，请在电脑端检查后重试"];
 i18n.setLanguage('en');for(const source of messages){assert.doesNotMatch(i18n.t(source),/[\u4e00-\u9fff]/,source);assert.equal(desktop.translate(source,'en'),i18n.t(source));}i18n.setLanguage('zh');
});
