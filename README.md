# ok-bd2

`ok-bd2` 是一个基于 `ok-script` 的 BrownDust II 图像识别与 UI 自动化辅助项目。
当前重点是 Windows PC 客户端的窗口连接、后台截图、输入测试、自动登录和登录后公告清理。

> 本项目仅用于个人学习和研究 Python、计算机视觉、OCR 与 UI 自动化。使用自动化工具可能违反游戏或平台服务条款，风险由使用者自行承担。

## 功能状态

- 自动寻找或启动 BrownDust II PC 客户端。
- 支持 WGC / BitBlt 等截图方式。
- 提供实时截图预览和输入测试任务。
- 启动游戏后自动触发登录流程，不需要在任务栏手动运行自动登录。
- 支持 BrownDustX Mod 管理器加载页与 Confirm 异常确认。
- 登录后并行识别 loading 页面和主页小屋按钮；检测到主页按钮时优先进入公告清理和主页亮度确认。
- 提供“自动登录状态”页面，实时显示阶段、匹配分数、OCR 文本和最后动作。

## 环境要求

- Windows 10/11
- Python 3.12
- BrownDust II PC 客户端
- 建议使用 16:9 游戏画面

## 安装与启动

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python main_debug.py
```

如果游戏路径或进程名和默认值不同，可以在启动前设置环境变量：

```powershell
$env:OK_BD2_GAME_PATH = "D:\Path\To\BrownDust II.exe"
$env:OK_BD2_GAME_EXE = "BrownDust II.exe"
$env:OK_BD2_HWND_CLASS = "UnityWndClass"
python main_debug.py
```

## 自动登录素材

自动登录模板位于：

```text
offline-train/train-source-screenshots/
```

当前代码期望这些文件存在，文件名应保持 ASCII：

```text
browndustx.png
browndustx-confirm.png
touch-to-start.png
image/UI_loading_black.png
home.png
guild.png
```

其中：

- `browndustx.png` 用于识别 BrownDustX 正在加载 Mod。
- `browndustx-confirm.png` 只使用底部 Confirm 区域，并结合 OCR 确认 `CONFIRM` 文本。
- `touch-to-start.png` 用于识别登录页。
- `image/UI_loading_black.png` 用于辅助识别登录后的过场加载页；`home.png` 的优先级更高。
- `home.png` 用于识别主页左侧小屋按钮，并作为亮度判断基准。

## 调试

常用任务：

- `BD2 截图 OCR 探针`：保存当前画面和 OCR 结果到 `probe_outputs/`。
- `BD2 鼠标单击测试`：按百分比坐标测试点击。
- `BD2 短按键测试` / `BD2 长按键测试` / `BD2 键盘调试测试`：验证输入方式。
- `自动登录状态`：查看隐藏自动登录触发任务当前进度。

运行测试：

```powershell
python -m unittest discover tests
.\.venv\Scripts\ruff.exe check .
.\.venv\Scripts\ruff.exe format .
```

## 仓库发布注意

默认忽略本地配置、日志、截图、探针输出、虚拟环境和上游参考源码：

```text
configs/
logs/
screenshots/
probe_outputs/
.venv/
upstream/
```

发布前请确认：

- `configs/` 中没有个人路径、账号或本地配置被提交。
- `logs/`、`screenshots/`、`probe_outputs/` 没有被提交。
- 自动登录素材中没有账号信息或个人隐私。
- `pyappify.yml` 和 `src/config.py` 中的项目链接已替换成你的实际仓库地址。

## 许可证

本项目代码按仓库内 `LICENSE` 发布。游戏名称、截图、图标与 UI 素材的权利归各自权利方所有。
