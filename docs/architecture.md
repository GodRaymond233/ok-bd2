# 项目架构

`ok-bd2` 是基于 `ok-script` 的 Windows PC 端图像识别自动化应用。项目自身负责
Brown Dust 2 的窗口适配、识图/OCR 规则、鼠标交互和任务状态机；任务调度、基础截图、
配置界面等通用能力由 `ok-script` 提供。

## 运行链路

```text
main.py / main_debug.py
        ↓
src.config.config
        ↓
ok.OK：GUI、设备、截图、OCR、任务执行器
        ↓
BD2Interaction + BD2Scene + BaseBD2Task
        ↓
触发任务 / 一次性任务
        ↓
截图 → ROI → 模板匹配或 OCR → 状态确认 → 鼠标操作
```

1. `main.py` 或 `main_debug.py` 创建 `ok.OK`，后者仅额外打开调试配置。
2. `src/config.py` 注册窗口、截图方式、交互类、场景、任务和自定义页面。
3. `ok-script` 连接游戏窗口并创建任务执行器。
4. `AutoLoginTask` 等触发任务维护登录状态；可见的一次性任务执行具体业务流程。
5. 所有正式游戏交互通过 `BaseBD2Task.operate_click()` 或鼠标滚轮/拖动完成。

## 分层与职责

### 1. 启动与装配

- `main.py`、`main_debug.py`：生产和调试入口。
- `src/config.py`：应用的组合根，只负责装配组件和注册任务。
- `src/game_path.py`：发现运行中的游戏或解析安装路径。
- `src/compat/`：对上游框架或 Windows 版本差异做窄范围兼容。

这一层可以依赖所有被装配组件，但业务任务不应反向依赖 `config`。

### 2. 平台适配与共享运行时

- `src/interaction/BD2Interaction.py`：BD2 的鼠标点击、滚轮、前台激活和光标恢复。
- `src/scene/BD2Scene.py`：跨任务共享的登录/战斗场景状态。
- `src/globals.py`：线程池和周期任务生命周期。
- `src/tasks/BaseBD2Task.py`：任务共同基类，提供截图、OCR、操作封装、动作节流、
  探针输出和标准快速选卡入口。

PC 客户端不能可靠接收自动化键盘输入，因此平台层和业务层都不得自行增加键盘发送、
按键映射或热键方案。

### 3. 纯识别与坐标工具

- `src/utils/template_resolution.py`：离线模板目录标定、基础倍率、绿幕目录标记和
  `Main*` 模板的共享搜索区域。
- `src/utils/image_utils.py`：灰度转换、模板/遮罩缩放、像素相似度、多倍率生成，
  以及相对/参考坐标 ROI 裁剪。
- `src/utils/ocr_utils.py`：OCR 文本归一化、关键词计数和容错子串匹配。
- `src/utils/vision_utils.py`：与框架 `Box` 相关的轻量坐标辅助。

这些模块不持有任务状态，不点击游戏，适合优先添加单元测试并被多个任务复用。
参考坐标 ROI 必须同时缩放左上角、右下角、宽高和位移，不能只缩放区域尺寸。

### 4. 业务任务

- `src/tasks/trigger/AutoLoginTask.py`：持续触发的登录状态机。
- `DailyTask`、`FreeGachaTask`、`PVPTask`、`SquareGoddessTask`：日常业务状态机。
- `BargainLevelTask`、`QuickSuppressionTask`：独立循环任务。
- `BD2ProbeTask`、`BD2InputTestTask`、`BD2DiagnosisTask`：诊断和适配工具。

任务类负责“先识别并确认状态，再执行鼠标动作”。底层图像算法应放入 `src/utils`，
稳定的跨任务页面流应放入 `BaseBD2Task`；只属于单个玩法的状态转换保留在对应任务中。

### 5. 跑图/跑商领域模块

跑图和跑商已按领域对象拆分在 `src/tasks/map_trade/`：

- `models.py`：不可变数据模型、界面状态和结果类型。
- `data.py`：卡带、商店、物品、坐标和模板规格。
- `vision.py`：该领域的模板/OCR 门面，复用通用识别工具。
- `navigator.py`：主页、选卡、地图、商店之间的导航状态机。
- `trader.py`：购买、出售、料理和收藏重建。
- `collector.py`：剧情卡带地图采集。
- `calendar.py`：北京时间下的价表和库存业务日期。
- `progress.py`：每日/每周断点进度的持久化与恢复。
- `MapTradeTask.py`、`MapCollectionTask.py`：配置卡片、依赖装配和阶段编排。

推荐依赖方向为：

```text
Task shell → Trader / Collector → Navigator → Vision → utils
                    ├→ Progress       └→ models / data
                    └→ Calendar
```

领域模块不应导入具体 UI 页面；UI 仅通过任务配置和状态字段观察任务。

### 6. UI、配置和资源

- `src/ui/`：状态页、实时截图、日志入口和响应式任务配置补丁。
- `configs/`：运行时配置与进度文件；不是业务常量的唯一来源。
- `offline-train/train-source-screenshots/`：正式模板资源。
- `assets/map_trade/`：随程序发布的跑商业务数据。
- `.local-dev/`：不发布的一次性探针、样本和实验输出。

## 模板识别约束

- 根目录模板在 1920×1080 客户区的基础倍率为 `1.0`。
- `image/` 下模板的统一基础倍率为 `1.25`。
- `image/green/` 自动启用纯绿与透明区域遮罩。
- 正式加载必须经 `offline_template_scale()` 计算当前客户区倍率。
- 识别到目标时点击匹配框中心；稳定固定位置才可使用相对坐标。

这些规则集中在 `template_resolution.py`，任务不得复制另一套目录倍率判断。

## 状态、失败与可观测性

- 跨任务的“已登录/战斗中”状态由 `BD2Scene` 保存。
- 单个任务的阶段和诊断信息通过 `info_set()` 暴露给状态页与日志。
- 跑图/跑商的持久进度由 `ProgressStore` 管理，并区分北京时间下的每日和每周周期。
- 页面确认失败必须返回失败或记录失败阶段，不能继续盲点。
- 重试应有次数或时限上限，避免无限点击。

## 扩展指南

新增任务时按以下顺序选择落点：

1. 纯坐标、图像或 OCR 算法放入 `src/utils`，先写单元测试。
2. 多个任务共享且长期稳定的页面流放入 `BaseBD2Task`。
3. 单个玩法的状态机放入独立任务；超过一个明确领域时再拆子模块。
4. 任务在 `src/config.py` 注册，用户可调参数放在任务的配置声明中。
5. 一次性探针和样本先放 `.local-dev/`，确认成为正式能力后再迁入源码和测试。

## 验证

开发完成前运行：

```powershell
.\.venv\Scripts\python.exe -m ruff check src tests
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

此外要检查 `src` 中没有项目自定义的键盘发送、按下、释放、热键注册或键盘映射实现。
