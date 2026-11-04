# Agent Note: 网关打包参数通过标准输入传递

Status: implemented

## 问题

PR #130 报告 Windows 的正式打包入口因大量 --add-data 参数超过 CreateProcess 的 32767 字符限制而失败。旧跨平台验收曾通过临时短路径和单独包装脚本绕过，不能据此证明仓库标准构建入口可靠。

## 决策

保留同一个独立 Python 打包进程，使用 PyInstaller 官方公开的 `PyInstaller.__main__.run(args)`，把原样参数列表通过 stdin JSON 传入。实际启动命令长度不再随资源路径数量增长，参数内容、顺序、收集规则和输出目录不变。所有系统复用同一实现，不添加 Windows 专用资源清单或依赖。

## 备选方案

继续缩短路径只推迟上限，资源增加后仍会失败。把全部打包改为手写 spec 会增加与现有参数维护的分叉。直接在构建父进程调用 PyInstaller 虽然也能避免上限，但可能改变其全局状态，因此保留子进程隔离。

## 验证

定向夹具执行实际子进程入口，向替身 PyInstaller 传递超过 32767 字符、包含中文和空格的五百组资源路径，验证解析后的参数列表逐项相同，启动命令仍短于上限。真实 Windows 冻结构建与该参数传输测试分别记录，不能互相替代。

## 影响

构建仍要求已有 PyInstaller 环境，不安装新工具；标准输入只携带构建路径与参数，不含用户凭据。失败仍通过子进程退出码传递。依据 [PyInstaller 官方用法](https://github.com/pyinstaller/pyinstaller/blob/develop/doc/usage.rst) 的程序化调用接口实现。
