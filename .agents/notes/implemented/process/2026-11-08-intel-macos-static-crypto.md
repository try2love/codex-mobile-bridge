# Agent Note: Intel macOS 冻结程序的 OpenSSL 隔离

Status: implemented

## 问题

原生 Intel macOS CI 的发布签名通信夹具启动失败：cryptography 的 Rust 扩展需要 `SSL_get0_group_name`，冻结包中的 `libssl.3.dylib` 未提供该符号。安装日志显示 cryptography 50.0.2 从源码构建，并动态链接 Homebrew OpenSSL；PyInstaller 收集的同名 Python SSL 库与之不兼容。源码测试成功不能证明冻结程序可运行。

## 决策

Desktop builds 仅在 Intel macOS 作业安装常规依赖前，以 `OPENSSL_STATIC=1` 从源码构建 cryptography 50.0.2，并禁用该次安装的 wheel 缓存。复用运行器已有 OpenSSL/Rust；不改变用户系统。后续正式打包与发布身份夹具使用同一个依赖环境。其他平台维持原有安装路径。

## 备选方案

退回仍提供 Intel wheel 的旧版本可以避免源码构建，但会固定到更旧的加密依赖，因此不采用。手动替换冻结包的共享 libssl 可能改变 Python SSL 及其他扩展的依赖，影响范围更大，也不采用。

## 影响

Intel 构建需要源码编译时间；静态链接避免多个 OpenSSL 动态库之间的同名冲突。此处显式版本需随依赖维护更新。原生发布身份验证、真实冻结程序通信与升级交接、DMG/ZIP 检查均保留，不降低任何认证检查。验收结果以修订后的 Intel CI 为准。

## 依据

[上游安装说明](https://cryptography.io/en/latest/installation/#building-cryptography-on-macos) 提供静态构建方式；[49.0.0 变更记录](https://cryptography.io/en/latest/changelog/#v49-0-0) 说明不再发布 Intel macOS wheel。失败见候选 Desktop builds 42472339912 的 Intel 作业。
