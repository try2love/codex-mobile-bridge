# Agent Note: 移动操作组保留内层阅读位置

Status: implemented

## 问题

历史前插/重新排序时，浏览器移动整个details组会重置其内部工具正文的scrollTop；仅恢复组自身滚动不够。

## 决策

复用现有capture/restoreChatActivityState，保存并恢复组内叶子details的展开与滚动。状态仍绑定当前会话DOM，不持久化授权或跨会话继承。

## 备选方案

拒绝销毁后整组重新渲染：会丢选区并增加闪烁。

## 影响与验证

真实生产渲染器回归先观测到leaf仍open但330px变0，修复后Chrome/WebKit宽窄屏8项阅读与滚动用例通过。原有reading夹具同步打开新增加的操作组，保持对真正可见内层滚动的检查。
