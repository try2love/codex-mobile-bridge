# 归属与第三方声明

- 网关基于 try2love/codex-mobile-bridge，基线提交 `cec08a0d20dac9a3f77374f01bec818e933345d7`。MIT 原文保留于 `app/LICENSE`。本仓库新增 Windows UI、双模式连接及手机使用功能，贡献部分同样采用 MIT。
- Project Nayuki QR Code generator：MIT；完整许可保留在 `vendor/qrcodegen.py` 文件头。
- Android 扫码使用 [ZXing Android Embedded 4.3.0](https://github.com/journeyapps/zxing-android-embedded)、ZXing Core 和 AndroidX（Apache-2.0）。声明和许可随 APK 保存在 `assets/THIRD_PARTY_NOTICES.txt`，对应源码路径为 `mobile/android/app/src/main/assets/THIRD_PARTY_NOTICES.txt`。
- markdown-it 15.0.2、KaTeX 0.18.9、markdown-it-texmath 1.0.0：MIT；许可和摘要位于 `app/web/vendor/`。
- Cloudflare cloudflared 2026.9.3：Apache-2.0；许可位于 `licenses/cloudflared-LICENSE`。固定版本 Windows amd64 摘要见 `scripts/fetch_cloudflared.py`。源码仓库不提交此二进制，Windows 发布包会包含该组件。
- Windows 发布包还包含 Python、CustomTkinter、Pillow、Paramiko、Cryptography、bcrypt、PyNaCl、cffi 等运行依赖。各自许可证随已安装分发包元数据保留，构建使用 PyInstaller（其许可证包含构建产物分发例外）。这些依赖不因根目录 MIT 而改变许可证。

本项目不包含 Codex Desktop 程序、提取出的桌面源码、OpenAI API 密钥或模型授权。OpenAI、Codex、Cloudflare 等名称属于各自权利人。
