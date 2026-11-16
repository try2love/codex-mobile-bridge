# Agent Note: 官方 OAuth 弹窗保持登录隔离

Status: implemented

## 问题

#174 报告的官方登录先打开 about:blank，占位窗口再异步导航到授权地址。原实现拒绝所有 window.open；仅允许官方 HTTPS 作为初始地址仍会失败。实际官方中国区授权入口为 zchat.z.ai，旧导航白名单也未包含它。

## 决策

允许精确空白占位页及已核验官方来源的子窗口。所有子孙窗口继续复用此次登录的临时 Session，并强制沙箱、上下文隔离、关闭 Node 集成；在首次导航前安装同样的跳转和弹窗来源检查。带用户信息的 URL、非 HTTPS 及非官方来源拒绝。主登录窗口关闭后销毁全部后代，不留下继续授权的窗口。

## 备选方案

全部放开弹窗会扩大外部内容权限；只改当前主窗口地址会破坏官方轮询及占位窗口存活检查。均不采用。此提交不添加令牌导入，也不改变已有加密存储。

## 影响

按实际授权行为工作，不限定 App 版本。官方页面会按链接自带 app_version 返回不同脚本，因此核验同时对照 latest 与 issue 的链接参数，不能把 latest 的不同协议当作旧页面实现。浏览器登录回执和新版存储结构分别修复。

## 测试

24 项控制器检查通过；真实 Electron 44.5.1 的合成 HTTPS 页面完成 11 项断言，覆盖空白占位、子孙同 Session、安全偏好、非法跳转拒绝和关闭清理。夹具未调用真实安全存储或登录，已正常退出。官方匿名隔离登录 init 请求确认中国/全球授权地址及回调路径，仅记录无状态值的来源摘要；未完成授权。新增中国区来源的定向测试通过。

## 相关资料

- [Issue 174](https://github.com/try2love/codex-mobile-bridge/issues/174)
- [Electron window.open](https://github.com/electron/electron/blob/main/docs/api/window-open.md)
- [官方 issue 对应脚本](https://zcode.z.ai/remote/v4/3.18.2/assets/index-Chh4bF09.js)
