# Agent Note: 云运行依赖随源码入口与构建验证

Status: implemented

## Problem

运行 Windows 源码网关的 Python 缺少已经声明的云依赖，出现 `cloud_dependencies_missing`；桌面解释器与安装依赖的解释器必须一致。

## Decision

源码说明要求先安装 Python requirements，再启动控制器，并说明 `CMB_PYTHON` 边界。构建在原生辅助编译或下载前检查依赖导入，显式收集 `noise`、`aiohttp`、`keyring` 和 `paramiko`；发行包 `noiseprotocol` 的导入名是 `noise.connection`。工作流路径过滤覆盖全部 requirements，不以新增依赖版本掩盖环境缺包。

## Alternatives considered

无；原记录只记录已声明依赖的补装与构建检查，未保存其他方案。

## Consequences

缺包在构建早期可见，源码解释器的选择明确；冻结包和真实云互通仍需分别验收。依赖可导入不意味着已登录、启用中继或批准手机。

## Testing

2026-11-01 在当时的 Python 3.12 安装 `aiohttp==3.13.3`、`noiseprotocol==0.3.1`、`keyring==25.6.0`，导入、Windows vault 后端初始化和 `pip check` 通过，检查未读写凭据。核对进程后使用实例令牌协作重启网关，HTTP 健康通过，状态由缺依赖变为无错误的 `disabled`，直连仍运行；未登录账号、启用中继或配对。

当时 24 项云 Python（含向量、隔离、通知与 3 项构建契约）、5 项资源布局和 1,322 项 Node 通过，无缺包跳过；固定临时密钥警告仅来自测试向量。未构建或发布包。那次仍存在的 `cloud_connector` 上层导入后来由[监听器组装](../architecture/2026-11-02-cloud-listener-injection.md)修复；旧失败记录不改成当时通过。
