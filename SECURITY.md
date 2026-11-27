# 安全政策 / Security Policy

## 支持的版本 / Supported Versions

`ok-bd2` 仅对**最新发布的稳定版本**提供安全修复与更新支持。请在反馈前确认你已更新至 [Releases](https://github.com/GodRaymond233/ok-bd2/releases) 中的最新版本。

## 报告安全漏洞 / Reporting a Vulnerability

如果你发现可能影响用户安全的问题（例如安装包供应链风险、任意命令执行、敏感信息未脱敏泄露等），**请勿在公开 Issue 或群聊中直接披露细节**。

请优先通过 GitHub 的私密漏洞上报通道提交：

- [Report a vulnerability](https://github.com/GodRaymond233/ok-bd2/security/advisories/new)

请在报告中尽量提供：

1. 受影响的 `ok-bd2` 版本与安装方式；
2. 问题的复现步骤、触发条件或概念验证（PoC）；
3. 潜在影响范围及（如有）修复建议。

维护者收到后会尽快评估并在确认后优先发布修复版本。

## 安装包验证与杀毒软件误报说明

### 1. 官方下载渠道

请仅从以下官方渠道获取安装包：

- [GitHub Releases](https://github.com/GodRaymond233/ok-bd2/releases)
- [README.md](README.md#安装指南) 中公布的官方网盘链接

请勿运行来源不明的二次打包版本。

### 2. 为什么可能触发安全软件提示？

`ok-bd2` 使用 PyAppify / NSIS 打包，并在运行时执行窗口截图与鼠标模拟操作，这类行为特征容易被 Windows Defender 或部分第三方杀毒软件误判为风险程序。

- 本项目全部构建与发布流水线均公开在 [.github/workflows/build.yml](.github/workflows/build.yml) 中，可随时审计。
- 正式发布的安装包支持通过开源代码签名（SignPath）与构建产物校验进行核验；如有疑虑，你也可以按照 [README.md](README.md#方式二从源码运行) 直接从源码运行。

## 隐私与数据边界

- **游戏交互边界**：`ok-bd2` 仅通过截图识别界面并模拟鼠标操作，不读取或修改游戏内存，不修改游戏文件。
- **问题报告脱敏**：程序内置的“生成问题报告”功能会限制日志与压缩包体积，并自动脱敏本机路径、邮箱与常见凭据，**不会导出原始配置文件、环境变量、进程列表、用户名或机器名**。
- **截图自查提醒**：报告中附带的游戏故障截图仍可能包含游戏内昵称、UID 或聊天画面，请在发送到公开渠道或群聊前自行检查确认。
