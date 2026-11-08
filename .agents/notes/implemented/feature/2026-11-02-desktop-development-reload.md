# Agent Note: 源码桌面开发重载只刷新渲染器

Status: implemented

## Problem

源码 UI 开发需要及时加载资源变化，同时避免重载破坏未提交输入、在途操作或意外启动网关。

## Decision

`npm run desktop:dev` 显式为未打包 Electron 开启监听；打包版不开放开发 IPC/preload。只监听 `desktop/index.html` 列出的本地资源和惰性 WSL 账号脚本，不监听 main/preload CJS、Python、运行数据或日志，不新增依赖/开发服务器。

CSS 新链接加载后才替换，HTML/前端 JS 排队刷新渲染器；未保存输入、聚焦控件、打开的对话框及在途 IPC 延后刷新，主进程在执行前再次核对写入和更新/setup。跨开发 IPC 只传页面、详情 ID 和滚动位置，不复制表单、凭据、配对或请求；重载标记防止启动钩子唤起已停止网关。

[上游 v2.2.0 集成](../process/2026-11-08-upstream-v2.2.0-integration.md)采用按需客户端扫描；初次加载和开发重载均不恢复旧版启动扫描，重载仍只恢复视图并避免重复启动网关。

## Alternatives considered

无；原记录未保存其他备选方案。

## Consequences

渲染器可安全加载开发资源，代价是有编辑或在途操作时不会立即刷新；Python 和主进程修改仍需要显式重启，开发监听不能当作运行时更新。

## Testing

原测试使用临时源码、假定时器/DOM 和模拟 Electron IPC，不操作用户窗口或启动应用。真实 Electron 的视觉验收仍需用户手工完成，原记录没有保存具体通过数量。
