<div align="center">
  <img src="icons/icon.png" alt="ok-bd2 icon" width="180">

  <h1>ok-bd2</h1>

  <p>一款基于图像识别的 <strong>BrownDust II</strong> Windows PC 自动化辅助工具。</p>
  <p>基于 <a href="https://github.com/ok-oldking/ok-script">ok-script</a> 框架开发。</p>

  <p>
    <img src="https://img.shields.io/badge/platform-Windows-blue" alt="platform">
    <img src="https://img.shields.io/badge/python-3.12-skyblue" alt="python">
    <a href="https://github.com/GodRaymond233/ok-bd2/releases"><img src="https://img.shields.io/github/v/release/GodRaymond233/ok-bd2" alt="release"></a>
    <a href="https://github.com/GodRaymond233/ok-bd2/releases"><img src="https://img.shields.io/github/downloads/GodRaymond233/ok-bd2/total" alt="downloads"></a>
    <a href="./LICENSE"><img src="https://img.shields.io/github/license/GodRaymond233/ok-bd2" alt="license"></a>
  </p>
</div>

## 免责声明

> [!CAUTION]
> 本软件为开源、免费的外部辅助工具，仅用于个人学习、研究 Python、计算机视觉、OCR 与 UI 自动化。
>
> - **工作原理**：程序通过识别用户界面、截图和模拟输入与游戏交互，不读取或修改游戏内存，不修改游戏文件。
> - **使用风险**：自动化工具可能违反游戏、平台或发行方服务条款。使用本项目产生的账号、数据、收益或其他后果，由使用者自行承担。
> - **项目关系**：本项目与 BrownDust II 的开发商、发行商及相关平台没有从属、授权、认可或合作关系。
> - **商业行为**：本项目不提供也不认可代练、售卖脚本、商业托管或其他营利性用途。

> [!WARNING]
> 在使用本工具前，请确认你理解并愿意承担第三方自动化工具可能带来的封号、限制登录、收益回收或其他处罚风险。

<details>
<summary>Disclaimer in English</summary>

This project is a free and open-source external tool intended for personal
learning and research around Python, computer vision, OCR, and UI automation. It
interacts with the game through screenshots and simulated input only. It does
not read or modify game memory and does not modify game files.

Automation tools may violate the game's, platform's, or publisher's terms of
service. You are solely responsible for any account, data, reward, or other
consequence caused by using this project. This project is not affiliated with,
endorsed by, or sponsored by the developers, publishers, or platforms of
BrownDust II.

</details>

## 主要功能

> [!NOTE]
> 项目已覆盖 PC 客户端启动、窗口连接、后台截图、自动登录、每日自动化、跑商与每周跑图链路；探针与诊断任务仅在调试入口（`main_debug.py`）提供。

- **主要优势**：支持 PC 客户端和常见 16:9 分辨率，游戏窗口无需保持前台。
- **自动寻找或启动游戏**：支持通过配置定位 BrownDust II PC 客户端。
- **后台截图支持**：支持 WGC / BitBlt 等截图方式，用于窗口识别和自动化判断。
- **自动登录流程**：可由脚本唤起游戏本体，且启动后自动识别登录页、加载页、确认弹窗和主页状态。
- **每日自动化流程**：自动进行公会签到、小屋签到、一键收菜、白嫖抽卡、广场女神像、自动PVP。
- **自动刷级流程**：在独立分组中提供刷砍价等级和刷压制等级任务。
- **每日跑商**：在第六章商店进出货，收藏进度按周记录，出售价目表实时更新。
- **每周跑图**：单独按周管理卡带地图采集，保留每日额度与断点续跑进度。
- **状态查看**：提供自动登录状态页，显示阶段、匹配分数、OCR 文本和最后动作。
- **调试辅助**：`main_debug.py` 额外加载截图 OCR 探针、地图读取测试、基础检查与诊断任务，便于排查适配问题。

## 运行环境

| 项目 | 要求 |
|---|---|
| 操作系统 | Windows 10 / Windows 11 |
| 游戏客户端 | BrownDust II PC 客户端 |
| Python | 从源码运行需要 Python 3.12 |
| 画面比例 | 强制要求 16:9 |
| 分辨率 | 1280x720, 1920x1080, 2560x1440, 3840x2160 |

## 安装指南

### 方式一：使用安装包

适合普通用户。前往
[Releases](https://github.com/GodRaymond233/ok-bd2/releases)
下载最新安装包。

- `ok-bd2-win32-China-setup.exe`：完整安装包，默认使用国内更新源。
- `ok-bd2-win32-Global-setup.exe`：完整安装包，使用 GitHub / PyPI 作为更新源。
- `ok-bd2-win32-online-setup.exe`：在线安装包，首次运行需要联网下载依赖。

请下载 `setup.exe` 安装包，不要下载 GitHub 自动生成的 `Source code` 压缩包。

### 方式二：从源码运行

适合开发、调试或二次适配。

```powershell
git clone https://github.com/GodRaymond233/ok-bd2.git
cd ok-bd2
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python main_debug.py
```

项目依赖以 `pyproject.toml` 和 `uv.lock` 为唯一锁定来源，
`requirements.txt` 与 `requirements-dev.txt` 均由 uv 导出，不应手工编辑。
源码运行时版本同样以 `pyproject.toml` 为唯一来源（当前为 `0.1.1`）；
PyAppify 发布 tag 只标识更新交付批次，详情见
[PyAppify 发布流程](docs/pyappify-release-flow.md)。
维护依赖时运行：

```powershell
.\scripts\refresh_dependencies.ps1
.\scripts\check_dependency_exports.ps1
```

如果游戏安装路径或窗口信息与默认配置不同，可以在启动前设置环境变量：

```powershell
$env:OK_BD2_LAUNCHER_PATH = "C:\Path\To\Browndust2Starter.exe"
$env:OK_BD2_LAUNCHER_EXE = "Browndust2Starter.exe"
$env:OK_BD2_GAME_PATH = "D:\Path\To\BrownDust II.exe"
$env:OK_BD2_GAME_EXE = "BrownDust II.exe"
$env:OK_BD2_HWND_CLASS = "UnityWndClass"
python main_debug.py
```

自动启动时程序会启动 Neowiz 的 `Browndust2Starter.exe`，再等待游戏窗口出现。
启动器路径会依次从显式环境变量、运行中的启动器、框架提供的当前运行路径同级或
父级目录、`PROGRAMDATA` 下的标准目录和 Windows 卸载注册表中查找，不依赖某一台
电脑的固定盘符。

> [!NOTE]
> `ok-bd2` 经 Neowiz Starter 启动游戏，无法保证 Starter 会把未知的 DX11 参数继续
> 传给游戏本体。因此程序会隐藏并禁用框架的“Launch with DX11”选项，避免该设置
> 看似开启但实际静默失效；是否支持强制 DX11 仍需后续实机验证 Starter 的参数转发。

## 使用前检查

> [!IMPORTANT]
> 为了提高识别稳定性，请在启动自动化前确认以下设置。

- 使用游戏FHD画质档位。
- 使用 16:9 分辨率，推荐 1920x1080 或更高。
- 在游戏【设置】中将卡带内角色显示设为“仅显示主角（女主）一人”，减少队友模型对识别区域的遮挡。
- 关闭显卡滤镜、锐化、帧率显示、录屏悬浮窗等会改变画面的叠加层。
- 程序运行时不要锁屏、息屏或让系统进入睡眠。
- 游戏窗口可以放在后台，但不能最小化，不能移动游戏窗口到屏幕外。
- 游戏窗口在后台的时候仅能调用鼠标模拟。
- 任务运行期间请避免移动或抢占鼠标。

## 使用指南与 FAQ

### 快速上手

1. 启动 BrownDust II PC 客户端，或确认游戏路径配置正确。
2. 启动 `ok-bd2`。
3. 在程序界面中选择需要运行的任务。
4. 如遇识别失败，先查看自动登录状态页和日志，再点击 **生成问题报告** 保留现场。

### 图文使用说明

#### 日常/周常总览与一键执行

打开侧栏中的 **日常/周常** 页面后，可以单独展开并配置各项任务，也可以点击
**一键完成日常**，按当前启用顺序执行公会、小屋、酒馆、快速狩猎、抽抽乐、广场女神像、PVP 和跑商。
页面顶部会显示当天剩余项目、上次执行时间和耗时。

<p align="center">
  <img src="docs/images/usage/task-overview.png" alt="日常和周常任务总览" width="960">
</p>

#### 快速狩猎

快速狩猎可以分别启用冒险航线、当前默认狩猎场和圣石洞穴，并设置双倍策略、资源倾向与米饭分配方式。
狩猎场会沿用游戏当前选择的关卡；圣石洞穴会读取五种圣石数量，优先处理数量最少的属性。
执行完整流程会实际消耗米饭和火把，请在启动前确认配置。

<p align="center">
  <img src="docs/images/usage/quick-hunt-config.png" alt="快速狩猎配置" width="960">
</p>

#### 自动 PVP

在 **镜中之战** 任务中设置竞技场战斗倍数和最多战斗轮次。程序会处理匹配、战斗结算和返回主页；
AP 不足时，单次战斗倍数会自动降为 1。

<p align="center">
  <img src="docs/images/usage/pvp-config.png" alt="自动 PVP 配置" width="960">
</p>

#### 每日跑商

每日跑商可分别控制买入、卖出、收藏管理、程序默认价表、出售保险及白名单/黑名单。

> [!IMPORTANT]
> 建议从已确认的游戏主页或剧情卡带 6 商人前启动跑商。程序会优先识别商人位置；
> 若当前在主页，则经快速切换进入剧情卡带 6。其他未确认界面可能导致任务安全停止。

<p align="center">
  <img src="docs/images/usage/trade-config.png" alt="每日跑商配置" width="960">
</p>

#### 自动刷级

**刷砍价等级** 从剧情卡带 6 商人处开始；**刷压制等级** 从战斗地图开始。
这两个任务独立于日常批处理，可以分别启用和运行。

<p align="center">
  <img src="docs/images/usage/auto-level-config.png" alt="自动刷级任务配置" width="960">
</p>

#### 截图、诊断与分辨率

在 **截图方式** 页面可以完成常用的运行前检查和故障反馈：

1. 游戏尚未启动时，点击 **启动游戏 (F9)**；游戏已启动时，该按钮用于识别并连接现有窗口。
2. 点击 **截图** 保存当前游戏窗口的原生 PNG，并打开截图目录。
3. 通过实时截图区域确认后台捕获结果是否正常。
4. 发生异常时立即点击 **生成问题报告**，按[问题反馈](#问题反馈)中的流程保留现场。
5. 使用分辨率选择器将游戏窗口调整为支持的 16:9 尺寸。

<p align="center">
  <img src="docs/images/usage/capture-and-diagnostics.png" alt="截图、窗口连接、问题报告和分辨率入口" width="960">
</p>

#### 安装后的自动更新

安装版启动后会检查云端版本并自动拉取更新；更新完成后启动主程序，并展示本次版本的更新日志。

<details>
<summary>查看自动更新界面示例</summary>

<p align="center">
  <img src="docs/images/usage/updater-progress.png" alt="自动更新进度" width="760">
</p>

<p align="center">
  <img src="docs/images/usage/update-changelog.png" alt="更新日志与主程序界面" width="960">
</p>

</details>

### 常见问题

**程序找不到游戏窗口怎么办？**

确认游戏已经启动，并检查 `OK_BD2_GAME_PATH`、`OK_BD2_GAME_EXE`、
`OK_BD2_HWND_CLASS` 是否符合你的本机环境。

**程序无法自动启动游戏怎么办？**

检查 Neowiz Starter 是否已经安装；非标准安装位置可通过
`OK_BD2_LAUNCHER_PATH` 指定完整的 `Browndust2Starter.exe` 路径。

**识别结果不稳定怎么办？**

优先检查分辨率、亮度、语言、显卡滤镜和窗口遮挡情况。截图识别依赖画面稳定，任何叠加层都可能影响结果。

**为什么不建议直接下载 Source code？**

GitHub 的 `Source code` 压缩包只是源码快照，不包含安装器、更新配置和离线依赖。普通用户应下载 `setup.exe`。

## 问题反馈

遇到异常时，优先在程序首页点击 **生成问题报告**：

- 程序会先保存最近的游戏窗口画面，再暂停当前任务，避免现场被后续操作覆盖。
- 用一句话填写问题现象，并确认是否附带预览中的游戏截图。
- 生成完成后，反馈文字会自动复制，ZIP 文件会在资源管理器中选中；把两者一起发送到群聊即可，不需要 GitHub 账号。
- 如需继续任务，请在结果窗口点击“继续运行”；直接关闭会保持暂停。

标准问题报告会限制日志、截图和总包体积，并自动脱敏常见凭据、邮箱和本机路径；不会导出原始配置、环境变量、进程列表、用户名或机器名。游戏截图仍可能含有游戏内账号或聊天内容，请在生成前检查预览，也可以取消勾选截图。

熟悉 GitHub 的用户仍可通过 [Issues](https://github.com/GodRaymond233/ok-bd2/issues) 提交，并附上生成的报告编号；首页“导出原始日志”仅作为旧版兜底入口，其内容不会自动脱敏。

## 开发者说明

```powershell
# 修改过程中只运行直接受影响的测试
.\scripts\run_checks.ps1 -Mode Focused -Tests tests.test_pvp_task

# 最终 diff 上一次性执行完整门禁
.\scripts\run_checks.ps1 -Mode Final

# 发布前增加依赖锁、导出和已安装依赖检查
.\scripts\run_checks.ps1 -Mode Release
```

`Focused` 只用于修改过程中的快速反馈；生产代码、测试、依赖、资源或工作流发生变化后，
必须在最终 diff 上执行一次 `Final` 或 `Release`。链接 worktree 可通过 `-Python` 或
`OK_BD2_PYTHON` 指定共享虚拟环境。

更多发布和架构资料见：

- [架构说明](docs/architecture.md)
- [发布检查清单](docs/release-checklist.md)
- [PyAppify 发布流程](docs/pyappify-release-flow.md)

## ok-script 生态

以下项目同样基于
[ok-script](https://github.com/ok-oldking/ok-script)
开发，可作为学习和参考资料：

- [ok-oldking/ok-wuthering-waves](https://github.com/ok-oldking/ok-wuthering-waves)
- [BnanZ0/ok-nte](https://github.com/BnanZ0/ok-nte)
- [BnanZ0/ok-duet-night-abyss](https://github.com/BnanZ0/ok-duet-night-abyss)
- [Shasnow/ok-starrailassistant](https://github.com/Shasnow/ok-starrailassistant)

## 致谢与开源说明

本项目基于 [ok-script](https://github.com/ok-oldking/ok-script) 开发，并参考了：

- [BnanZ0/ok-nte](https://github.com/BnanZ0/ok-nte)：README 结构、项目布局与 PyAppify 发布形态。
- [ok-oldking/ok-script-app](https://github.com/ok-oldking/ok-script-app)：ok-script 应用模板、任务示例、i18n 和打包配置。
- [ok-oldking/ok-wuthering-waves](https://github.com/ok-oldking/ok-wuthering-waves)：成熟项目中的任务注册、自定义页面、日志和更新流程。
- [JZPPP/MaaBD2](https://github.com/JZPPP/MaaBD2)：BrownDust II 地图采集链路参考。


第三方开源依赖、参考项目与打包组件见
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。

## 许可证

本项目代码按仓库内 [LICENSE](LICENSE) 发布。

游戏名称、截图、图标与 UI 素材的权利归各自权利方所有。本项目不主张对这些第三方素材拥有任何权利。
