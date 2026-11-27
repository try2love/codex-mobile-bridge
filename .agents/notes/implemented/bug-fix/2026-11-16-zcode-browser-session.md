# Agent Note: 官方网页登录结果按结构兼容

Status: implemented

## 问题

#174 对应官方页面的登录成功结果已改为 oauth:browser_session，并增加中国区 zai_cn。原捕获只读旧键、只接受 zai 与 bigmodel，即使弹窗授权成功也不会保存新结果。

## 决策

优先读取官方主登录记录的 provider、revision、userInfo 和 zcodeJwtToken；按字段能力判断，不根据 App 版本号选择实现。只有主记录不存在时才读取旧键。主记录损坏或未完成时保持等待，禁止回退到遗留的其他账号。中国区 provider 沿用现有加密保存、恢复和中继 JWT 链路。

## 备选方案

同时搜索多个键并取第一个 token 容易把旧账号重新接入。仅追加 provider 枚举不能解决新存储结构。直接导入系统浏览器 Cookie 或 token 没有必要，均不采用。

## 影响

既有加密文件兼容；无新增凭据文件和明文持久化，不改原 App 的账号。授权代际、目标链接、窗口存活与文件身份核验继续阻止迟到的保存。此提交不等同于完成真实账号授权验收。

## 测试

27 项控制器检查通过。新增测试实际执行注入脚本，覆盖三个 provider 保存与恢复、旧键兼容，以及损坏/未完成/未知 provider/空 token 不回退。独立真实 Electron 合成页面验证新主记录及中国区捕获，使用假的加密适配器，不调用真实钥匙串；夹具正常退出。

## 相关资料

- [官方公开页面实现](https://zcode.z.ai/remote/v4/3.18.2/assets/index-Chh4bF09.js)
- [弹窗修复](2026-11-16-zcode-login-popups.md)
