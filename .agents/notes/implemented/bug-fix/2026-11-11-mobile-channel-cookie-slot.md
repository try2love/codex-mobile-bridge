# Agent Note: 手机同网关信道共用 Cookie 存储位置的迁移

Status: implemented

## 问题

iOS 打开已保存电脑与活动页换线都先验对端 HMAC，再写目标 origin 派生 Cookie。同主机的不同端口共用按 domain/path/name 标识的 Cookie 位置，Secure 还会改变 HTTP 下的可见性。旧目标冲突检查把可解释的源值当成陌生绑定；即使通过，目标写入改变源 Cookie 后，旧来源快照又会拒绝迁移。Android 的同一流程也需准确处理该变化及异步代次。不同域的对照成功，不能据此把主机名当作网关身份。用户最新明确绑定反馈来自 Android；实际地址尚未取得，本缺陷不代表全部连接失败的原因，iOS WK 实测也不替代该手机验收。

## 决策

候选仍须通过原信道 HMAC 挑战。迁移只接受当前绑定清单允许的目标，并在写入前、写入回调、peer 提交时复核页面/票据、完整根凭据名称和值、原锚点和当前清单。主文档导航复用现有信道票据使旧操作失效；iOS 仅当前主 WebView 的 didCommit 生效，Android 仅当前主 WebView 的 onPageStarted 生效，隐藏目标页不改变源票据。

目标存储位置已有值必须可由同网关的当前根派生值或已核验保存凭据解释。不同 gateway 的目标记录、名字不一致或未知 Cookie 仍拒绝，即使主机相同或值碰巧相等也不覆盖。iOS 按完整存储位置检查全部 Cookie；Android 以同一 host 的 HTTPS Cookie 视图检查 HTTP 下不可见的 Secure 值。它只读取原生 jar，不发 HTTPS 请求；token 仍绑定原目标 origin。写入也通过该存储视图提交，Secure 属性仍按原目标 scheme 设置，不把 HTTP 目标改成 HTTPS 网络地址。[Android CookieManager 官方文档](https://developer.android.com/reference/android/webkit/CookieManager)明确按 host/path/name 替换，Secure 值的写入 URL 必须为 HTTPS。

写后目标必须是本次派生值。源和目标共用位置时，源只接受这一次写入可解释的变化，包括 HTTPS 写入使 HTTP 源不可见；不同位置仍须保持原来源快照。失败恢复与提交资格分开：页面/票据失效拒绝提交，但根、锚点、目标保存凭据及实际写值未变且本次仍拥有目标位置时，仍可恢复自有写入，避免给原地址留下目标 token。每次写入只在内存以 UUID 声明目标位置和依赖的来源位置，新操作会接管；即使新旧 token 相同也不能由旧回滚覆盖。iOS 在 MainActor 上同步复核并调用 SDK 完成回调式 setter，将恢复入队后才撤销本次 owner，避免异步 overlay 入场前失去所有权；Android 使用同一排队顺序。完成或失败只清自己的 owner，页面清理、退后台和旧 peer 接替也触发受保护回滚。后到的新 Cookie、凭据或新操作优先，不清除绑定以绕过冲突。

iOS peer ready 通过现有原生 prompt 等待 WKStore 复读后再提交，不改共享 Web 消息格式。原 prompt 只答复一次，四秒未完成先返回 expired；较晚的 Cookie 回调不能再提交或再次答复。等待中重新核对当前 WebView、原 URL、连接 token 和前台状态。原私有 moveChannel 返回本来创建的 Task，生产调用方忽略它；原样提取夹具可等待真实任务完成，不另建任务管理器或重试通路。[Apple WKHTTPCookieStore 文档](https://developer.apple.com/documentation/webkit/wkhttpcookiestore)提供异步读写及完成回调；实际 prompt 与 Cookie 回调是否相互阻塞由本轮原生验证确认。

## 备选方案

单按 hostname 放行能消除提示，却会合并同一 Mac 上不同网关或陌生 Cookie。只删除来源快照检查会掩盖新登录与旧操作覆盖，因此改为准确的写后投影并保留根、票据和目标复核。清空绑定重配会丢失原电脑和账号关系，不采用。复制共享 Web 的 handoff 或另建迁移管理器会扩大协议，本点沿用原 peer 和消息合同。

## 验证

原诊断提取原样 Swift 方法，用真实 Foundation 临时 jar 连续 20 次复现两阶段失败及根凭据 await 竞态，结果保留，不重复未变复现。修后在现成 iOS 26.2 iPhone 17 模拟器的独立测试 bundle 中，原样提取 moveChannel、peer 提交与源 didCommit，Cookie 由真实非持久 WKHTTPCookieStore 保存，系统偏好/凭据只用内存替身，无正式手机或 Keychain 数据。

原生分段回执覆盖打开/活动两入口的同 host 跨端口、HTTP/HTTPS 双向和不同域同网关；不同 gateway 与未知值仍零写入，取 Cookie 时换根也零写入。其余段确认新登录的 Cookie 被接受后才保存新根，旧写入回调与真实同步 prompt 均保留新根和新 Cookie；旧操作没有提交目标绑定。失败恢复源值、后到 Cookie 保留、旧票据/锚点拒绝、隐藏目标页加载不取消合法切换、同 URL 主文档刷新拒旧提交、四秒期限后的迟回调只答复一次，以及两个切换仅最新票据写入/提交均通过。

前三次未完成的原生报告保持原样：前两次用非 HttpOnly 的假登录 Cookie 替换生产 HttpOnly Cookie，被 WK 拒绝；第三次的原源 Cookie 也有相同夹具属性差异。仅改 HttpOnly 一项的原生对照确认替换行为，随后正常源与登录播种对齐服务端 name/domain/path/Secure/HttpOnly，完整登录顺序先 Cookie 再根，不放宽断言。并发段最初把第二目标漏出合成允许清单，拒绝是正确结果；补齐夹具后仅补该段。它们不是产品修复证据的替代。

复审另发现旧票据同时阻止回滚的 P1；新增四个真实 WK 受控反例确认：只有票据改变时源 Cookie 恢复到保存绑定；新操作写相同 token 时旧回滚不写；回滚已入队但回调未返回时插入新操作仍提交成功；同窗口的新登录 Cookie、源保存绑定与根均保持新值。各段最终 owner 表为空，不据最终页面未提交代替 Cookie/凭据一致性。

P1 修后完整 iOS 模拟器目标无签名增量编译通过，受影响的回调/回滚/prompt 组通过；既有 channel-link/handoff、连接入口与资源定向 20 项通过。未重复未变的 enrollment/HMAC proof 检查。

Android 原样 moveChannel/rollbackPeer/Peer 在项目内官方来源 JDK 17 的 fresh 私有 home/tmp 下完成 22 项，通过单/双向 scheme、两个入口、不同身份/未知值拒绝、后到登录、回滚、同值新 owner 与并发检查。测试用真实 JDK jar 匹配 domain/path/Secure，并把其 RFC2965 的引号及 `$Version` 元数据转换成 Android getCookie 的头格式；此前最小对照已确认值本身未变。JSON stand-in 缺 quote、模板引号转义及完整目标 URL 期望的早期失败分别保留，产品未为它们放宽断言。最终冻结包为 `android-handoff-r7.zip`，产品 SHA 始终与 r3 相同；javac/java 均正常 exit0，原始 PID 以实际 result 为准。Windows 传输被审批拒绝后未改工具或路径重试，而采用无出站的本机运行；Windows 从未暂存或执行。该证据仍不是 Android WebView、APK 或真机验收。

分段日志、生成源码、属性/头格式对照和最终本机 `java-local-r7-receipt.json` 均保留在 `.tmp/feedback-20261111-10/mobile-binding/`。

2026-11-12 复用旧 Android 35 Pixel 8 虚拟设备，以只读、不保存快照方式启动。独立包原样提取 moveChannel、rollbackPeer 和 Peer，使用真实 Android CookieManager、WebView 124.0.6367.219、SharedPreferences 与 Android Keystore，仅保存合成凭据。十五项通过：打开/活动两入口跨端口、HTTP/HTTPS 双向与不同域迁移，陌生网关/未知 Cookie 拒绝，失败与旧票据回滚，以及新登录优先。连接回调是受控结果收集器，本组未执行真实网关 HMAC 探测或网页 handoff。v2.2.0 原样 moveChannel 在同环境第一项 HTTPS 同主机跨端口即返回原地址，目标提交断言失败；不是单凭新版通过推断旧版有问题。回执在 `.tmp/feedback-20261112/android-native/result.json` 与 `baseline-result.json`，旧设备与实体手机未覆盖安装。

## 影响

同网关的已证明别名可以正确使用共享 Cookie 位置，未知值与不同身份仍保持拒绝。旧异步操作不获得覆盖新根或新票据的提交权限，正常聊天和草稿继续沿既有页面 handoff。此改动属于原生手机壳，桌面包更新不能使已安装手机 App 获得修复；本点没有发布或安装正式手机版本，也未宣称真实手机报告已经完整复现或解决。

## 相关记录审计

- [按网关绑定选择访问地址](../architecture/2026-11-02-mobile-gateway-channels.md)：部分重叠，保留 HMAC/根/锚点和原页面交接，只校正原生 Cookie 位置与异步提交条件。
- [已连接手机优先回局域网](../feature/2026-11-02-prefer-lan-channel.md)：选择策略不变，已过 Wi-Fi/公网反馈不重复或扩展为本点验收。
- TCC、远端 CLI、DSH 和正常退出草稿与本点无关，保持原样。
