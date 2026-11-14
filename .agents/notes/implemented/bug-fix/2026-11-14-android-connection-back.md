# Agent Note: Android连接探测页返回电脑列表

Status: implemented

## 问题

#164 的自动重连探测页尚未创建 WebView，handleBack 将它误认成电脑列表，触发双按退出。

## 决策

根据既有 entryProgress 区分探测页，返回调用 home/clear 原路径；复用取消探测、Cookie切换票据和generation，使迟到回调不能重新连接。探测页补回顶部电脑列表入口与更多菜单；无WebView时仅提供电脑列表、通知收件箱、手机设置，不调用Web操作。

## 验证

源码定位确认原故障分支；后续使用本轮APK在既有Android模拟器验证实际Back、顶部菜单与晚响应。没有修改手机绑定或网络鉴权。
