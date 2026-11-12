# Agent Note: Codex APP 与 CLI 按原生来源选择执行通路

Status: implemented

## 问题

旧规则将 CLI 来源视为创建信息，继续前先寻找 App owner，再决定跟随 App 或恢复 CLI。纯 CLI 场景因此依赖 App IPC 的响应；同一来源在不同时间也可能使用不同执行通路。用户在本轮明确选择 APP 来源走 APP、CLI 来源直接走 CLI，并接受原 App 暂时不能同时写入 CLI 聊天的限制。

## 决策

Codex 使用已保存原生记录的来源选择执行通路。APP 来源继续使用原 App owner；CLI 来源仅使用所选主机及 CODEX_HOME 的原生 CLI daemon，不探测或复用 App owner。CLI 的继续能力只取决于该配置的 CLI 可用性，已有 App owner 不能授予此能力。显式继续才允许原生接入与冷恢复；被动历史读取不连接 App、不恢复 CLI、不导航桌面。

原生 0.167.0 的 `codex exec` 实际记录使用 `originator=codex_exec`、`source=exec`、`thread_source=user`。共享来源分类和 SQL 可见清单明确接受这个已核验的 CLI 标识，使全部/CLI 列表、直接读取及继续采用相同归属。不会仅凭 `source=exec` 或未知/近似 originator 授予接入；原生子代理标记仍先排除。目录、原 ID、provider 和原生记录不改写。

默认与独立本机入口都接受已安装的完整 CLI；优先使用该 home 的受管包，没有受管包或 socket 时可经 PATH 中的已有 CLI 调用原生 daemon start，随后仍核验返回 socket、握手 home 及原会话身份。允许已安装 CLI 与清除继承的 CODEX_SQLITE_HOME 分开：默认入口保留原环境，独立入口继续清除此变量，避免读入默认配置的数据库。SSH 仍使用目标主机自身的 CLI。

网关中明确配置的 Codex 程序路径同时传给本机 CLI 适配器；缺省时才采用已有发现规则。此前组合层丢弃该设置，导致用户即使指定完整 CLI，冷启动仍可能选到 App 的零散更新文件。Windows 实机确认这种文件缺少原生包清单，不能初始化 daemon；不伪造清单或拼装临时包。路径回归覆盖无 PATH、无桌面发现时使用显式 CLI，默认 PATH 接入与独立配置的数据库环境规则仍保留。

CLI 连接失败或原生 writer 冲突保留为当前 CLI 操作的失败，不切换到 App。原生锁、host/id/cwd/provider 核验、原认证、未知写入不重放及释放回执保护继续生效。只读状态与通知不构成激活授权。本决定只改变执行路由，不新增账号迁移、共享服务重建、强制释放或通知能力。

IDE 保留现有归属规则：先确认原 VS Code owner，已有 owner 但快照未到达、归属未知或身份不匹配时不另起执行器；确认缺席后，只有显式继续才允许原生接续。并发探测复用、代次失效及等待期限继续用于此路径。

## 备选方案

继续优先跟随 App 能复用已经持有 CLI 聊天的执行器，并减少原生 writer 冲突，是旧方案的主要理由；但它保留了用户本轮明确取消的来源与执行通路不一致及 App IPC 依赖，因此不再用于 CLI 来源。CLI 失败后自动回退 App 可提高部分场景的连接成功率，但会再次改变通路，也不采用。IDE 有独立双端同步要求，保留其已经验证的 owner 路径。

## 验证

`tests.test_cli_bridge_integration.CliBridgeTests` 的 41 项定向回归通过：存在 App owner 时 CLI 仍直接接入原生 daemon；CLI 原生拒绝不导航或复用 App；只读历史不连接任一执行器；迟到 Desktop 快照不能改写已接入 CLI。旧 owner 发现的竞争、等待、断连及身份用例改为明确的 IDE 来源，验证该边界继续有效。

释放后再继续的路由回归 1 项通过；提升测试执行权限后，隔离回环 HTTP 的 3 项回归通过，覆盖登录、CSRF 与显式继续。没有发送真实模型请求、停止正式 App/共享 CLI，或操作固定评估样本；打包及真实客户端验收另行记录。

复审新增 PATH-only 回归先复现默认入口 available=false；修后 4 项覆盖无 App/socket/home 包的冷继续、不同 home 和 socket 拒绝、默认数据库环境保留及独立入口清除，既有运行时环境用例 1 项也通过。macOS 已安装 CLI 0.167.0 经实际 help、生成 schema 与[同版本官方启动实现](https://github.com/openai/codex/blob/rust-v0.167.0/codex-rs/app-server-daemon/src/lib.rs)核对：隔离 home 的零轮次线程在无受管包/socket 时由默认 Bridge 从已有 PATH CLI 启动并恢复；经身份核验的 SIGHUP 正常退出后，新代 daemon 再次恢复同 ID/provider/cwd，turn/start 为零。两代自有进程均已核验不存在；证据位于 `.tmp/c10/native-path-cold-result.json`。此证据不替代 Windows/Linux 真机验收。

Test.10 的实际来源预检使用同一个全新私有 home，通过真实 0.167.0 `exec --json` 建立两个会话，各一次回环合成响应、零付费。原 JSONL 与 SQLite 均记录上述 exec 来源；旧分类把它们判为 other，all/CLI 清单为空，预检在启动打包网关前停止。独立观察确认两个原生进程与夹具进程退出、私有端口关闭、正式和旧 Test GUI 进程出生代不变。新增 store 与 Bridge 合同回归从这组三个原生来源字段复现遗漏，覆盖 all/CLI/get、共享列表与显式 CLI 接入，以及未知/无 originator、近似标识和两类子代理拒绝。原生历史保留在 `.tmp/feedback-20261111-10/mac-test10-cli-browser/realm-r1`，未为测试改写 source；修后的实际打包 HTTP/浏览器闭环仍须由新冻结候选续验，不能把合同夹具当成已完成的包验收。

## 影响

CLI 的接入行为随原生来源保持稳定，纯 CLI 不再等待 App owner。代价是 App 已持有同一线程时可能收到原生 writer 冲突；只有原生解除占用后才能继续 CLI，不将冲突伪装成接管成功。该决定不保证 App 与 CLI 同时写入同一聊天。

## 相关记录审计

- [现有执行归属路由](2026-10-14-cli-existing-owner.md)：部分取代，仅翻转 CLI 先寻找 App 的规则；保存旧理由、原生 writer 冲突证据及身份保护。
- [CLI 冷恢复](../feature/2026-10-14-codex-cli-resume.md)：部分重叠，原生 daemon start、原 ID 恢复和只读边界保留；其旧 owner 路由指引由本决定替代。
- [在途归属查询复用](2026-10-14-cli-connect-retry.md)：部分重叠，原生启动去重保留，owner 查询等待与代次保护继续用于 IDE。
- [VS Code 共用原会话](2026-11-03-vscode-shared-owner.md)：边界互补，IDE 的原 owner 与双端同步决定不变。
- [CLI 释放与安全恢复](2026-11-10-cli-release-and-safe-recovery.md)：部分重叠，仅 CLI 再继续时的 App owner 优先规则由本决定替代，其他回执与维护规则不变。
- 共享 Desktop 运行时提案及 macOS TCC 提案不属于本次路由改变，保持不动。
