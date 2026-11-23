# Agent Note: 真实版本UA下的Android离线桥识别回归

Status: implemented

## 问题

设备详情增加真实App版本后，Android UA由BridgeMobile/0.1-Android变为BridgeMobile/实际版本-Android。注入的mobile-offline.js仍使用旧字符串精确匹配，误判为iOS，调用prompt通道，导致弹窗、请求超时和前台状态未建立，聊天一直连接中。旧单元夹具只使用0.1版本，浏览器夹具直接替换原生接口，漏掉了这条入口链。

## 决策

注入脚本按有边界的BridgeMobile版本与Android后缀识别平台，兼容新旧版本；Android继续使用原MessagePort，iOS继续原prompt桥。token、主框架、原生origin和端口校验保持。只修改注入脚本中的固定版本识别，不放宽原生权限或将缓存设为登录前提。

回归使用实际注入脚本与新版完整UA建立MessageChannel，走认证、缓存读取、真实HTTP会话列表和最近正文，检测任何prompt调用。单元测试同时覆盖旧Android、新Android和新版iOS，避免只验证界面宿主提示。

## 备选方案

把UA退回0.1可暂时恢复但会丢失真实App版本，并保留固定版本误判。出现连接问题时清缓存或重新绑定无法修复平台分支。直接替换成模拟原生对象会再次跳过出错入口，因此测试实际注入脚本的通道选择和握手。

## 影响

Android需要覆盖安装携带修正注入资源的新包；网关无法替换旧APK里的注入脚本。无需卸载、清缓存或重绑。iOS桥协议不变；有版本的Android UA不会弹出iOS桥提示。

回归先复现新版UA的15秒超时，再验证端口认证、列表与正文；浏览器Chrome/WebKit宽窄8项通过，旧版与iOS单元通道通过。真实Android硬件未由自动测试代替，安装后的设备体验仍需实测。

关联：[可读设备信息](2026-11-23-readable-gateway-device-identity.md)继续以安装包元数据展示版本；[任务卡片生命周期](2026-11-23-history-transfer-preparation-panel.md)单独处理完成卡片关闭。
