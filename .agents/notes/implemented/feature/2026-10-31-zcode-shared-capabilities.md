# Agent Note: ZCode 复用聊天能力与账号事务

Status: implemented

## 问题

[手机原型](2026-10-31-zcode-mobile-test.md)仅开放核心收发，模型、Skill、提醒和账号入口仍被关闭。用户在手机上看到第四客户端后，无法使用其他客户端已有的共享能力。继续为 ZCode 另建页面会重复模型目录、草稿、通知和账号事务规则。

## 决策

ZCode 适配器只转换原生协议与数据；聊天继续使用 DesktopSessionsView，账号继续使用 DesktopClientAccountsPanel 和 DesktopSessions 的事务入口。能力来自已核验的原生接口，不以客户端名字复制业务。

模型目录来自原 App 的 modelSelectionService。共享目录缓存包含账号及 provider_config.json 身份；界面只读实际会话 modelSelection，不能把全局 preferredSelection 当作执行模型。切换使用 switchModelConfig，携带原生 revision/logEpoch 做 CAS，任务、待处理交互和队列非空时拒绝。原生明确返回 noop/config.unchanged 时，适配器转换为共享 accepted 并保留 nativeAck；这表示目标配置已经一致，不能误报为未知结果。其他回执不套用这一例外，未知写回执继续使用已有幂等记录，不自动重放。

Skill 来自当前会话冻结目录 getSkillReferenceCatalog，发送前重新核验 ID、enabled 和 session authority。按原生 composer 的 美元符号名称与文件路径组成的 Markdown 链接 格式生成引用，不复制 Skill 正文或自造附件提示。共享上传入口按 uploadFiles 能力放行普通文件，删除仅 Claude CLI 可上传普通文件的旧条件。

提醒按钮上提到共享聊天输入区，复用通知设置对话框。进入聊天和保存时读取策略，不增加独立定时器。ZCode turnHeader 的状态与单调 rowId 进入现有完成通知基线；打开旧历史不补发。扫描、收件箱、推送使用同一 provider 映射，Web 与两端原生壳同步允许 ZCode 通知路由，继续核验认证电脑与 URL 边界。

### 账号与进程

账号导入仅在桌面网关开放，手机只列出和切换保存的账号。共享账号面板按 quota 能力跳过不受支持的额度详情查询，避免对未保存的当前账号逐项请求并误报不存在。保存原生 `enc:v1` 凭据，不解密复制令牌；这些快照只适用于当前电脑。恢复只替换 OAuth/JWT 与账号域相关设置，清除旧账号派生 project-key，保留独立 API、项目、历史及外观设置。文件使用已有私有原子写入，切换使用共享任务保护、正常退出、备份、恢复、原生身份核对和失败回滚。

参考 [Cockpit Tools 账号事务](https://github.com/jlcodes99/cockpit-tools/blob/main/src-tauri/src/commands/zcode.rs) 的保存后重启流程，但不复制旧版 provider 格式。ZCode 3.17 使用 providerFamilyConnectionSelections，与旧 modelProviderFamilyModes 有差异。

账号重启前即时读取原生项目清单、当前进程内会话和正在使用的投影，核对队列、审批、执行和后台工作。当前原型只对已绑定的唯一原生聊天有完整 V4 队列证据；检测到其他会话、远端 host、多 runtime 或清单变化时拒绝自动退出。这是当前接入边界，不能把持久化 idle 当作所有任务已结束。扩大原生会话范围时需先补齐每条会话的实时证据。

真实验证发现 ZCode 把 `ps comm` 改成显示名，原 macOS 完整路径匹配漏报运行中进程。共享平台扫描对非绝对路径名称使用同用户内核 image 身份；只读取现有 Native.identity，不调用任何信号操作。退出后必须观察旧 PID 消失；仅恢复文件和身份相同不能算正常重启通过。

### 既有记录核对

- [手动协议原型](2026-10-31-zcode-native-session-prototype.md)、[手机原型](2026-10-31-zcode-mobile-test.md)：部分取代。本篇开放已核验能力与私有账号快照；原生 owner、版本绑定、loopback、固定聊天等约束保留。
- [Android 后台通知](2026-10-27-android-background-notifications.md)：互补，继续使用既有游标、基线和投递机制，不另建 ZCode 推送通道。
- WSL 账号、本机 CLI 同步和 Codex 模型环境笔记：运行环境不同，本轮不改变其账号或进程规则。

## 备选方案

- 沿用原型全部关闭能力，改动最少，但用户必须频繁回电脑，且已有共享选择器无法复用，因此接入原生目录与命令。
- 直接复制 Cockpit Tools 的完整账号实现，能快速获得成熟操作入口，但旧版配置格式、凭据加密重实现及独立重启事务会增加分叉，因此只参考流程并复用 Bridge 事务。
- 用持久化任务状态判定空闲，读取便宜且覆盖历史广，但不能证明内存队列和审批已清空，因此遇到未覆盖会话时阻止自动退出。

## 验证

协议 14 项、ZCode Python 18 项、共享 UI 267 项、通知相关 110 项、macOS 进程/正常退出 11 项通过；通知测试中 10 项先受沙箱回环监听限制，放行后只补跑这些失败项。手机共享源码 62 项及 Java/Swift URL 原生边界检查通过，iOS 通知资源选择与生命周期夹具包含 ZCode。

本机 ZCode 3.17.1 实测目录读取约 0.1 秒，受支持任务空闲核验约 0.3 秒。保存当前账号、正常退出旧 PID、恢复同一原生账号和启动新 PID已核验。冷加载未立即获得共享投影时，显式重新接入后确认原会话仍为 GLM-5.3-Flash / low；没有发送模型请求或重放旧命令。双账号快照隔离、配置保留与外部修改后的回滚阻止通过夹具验证，第二个真实登录账号未验证。

最终 Mac Test 为 2.1.7-test.20261031.zcode6，运行代码提交 423d5fb；冻结模块、原生传输资源及 375 个 Web 文件与源码匹配。安装保留正式网关、原生客户端进程、账号配置、网络设置和草稿。真实页面验收修复了同模型 noop 回执误报、未支持额度查询仍自动请求两个问题，分别通过 7 项 ZCode 移动合约与 34 项账号界面回归。

HTTPS 实发前即时核验原生执行器为 GLM-5.3-Flash / low：所选 Skill 按原生引用进入原会话，收到预期回复并清空发送草稿；完成提醒开启后产生 ZCode completion 事件，HTTPS 收件箱可读取。测试后恢复提醒为原来的跟随全局。局域网读取同一最新回复、模型和策略通过，390 像素布局没有横向溢出。iOS 设备构建通过，但设备不可连接，未安装新壳，因此不宣称手机系统推送已验收。

## 影响

已有聊天、账号和通知组件承接第四客户端，未增加 ZCode 专属刷新循环、聊天页或模型执行器。新增账号文件格式、原生 provider 目录及内核进程发现需要维护；私有快照不能跨电脑搬运。仍属 Mac Test 原型，固定会话、版本与临时 loopback 的范围不变；不宣称任意会话、新建聊天、跨平台原生接入或第二真实账号均已通过。手机原生壳的通知路由更新需要安装对应新壳后生效，网页聊天入口由网关更新提供。
