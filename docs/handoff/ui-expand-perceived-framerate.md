# 展开动画目测帧率 Handoff（ui-expand-perceived-framerate）

> 任务域：任务卡展开/收起动画的**目测（呈现端）**顺滑度。与 `ui-expand-transition.md`（快照回放层，含 P0 收回残影与交接闪回）是同一问题的不同侧面 —— 那份文档记录"回放层载体"路线，本文件记录"重定时原生链"路线与二者的收敛决策。**接手请先读那份，再读本文件。**

## Goal

消除用户目测到的展开/收起卡顿。明确口径：**问题在呈现端而非生成端** —— 应用内计时器帧率再高，若写入与 vsync 无相位关系、单帧位移过大、或每帧成本超过 vsync 预算，目测仍然卡。用户诉求同时包含动效审美（"慢起步 → 加速 → 缓冲落定"的苹果式手感）。

## Current State

- **⚠️ 硬件事实更新（2026-08-24 用户告知）：显示器与系统刷新率已从 120Hz 升至 180Hz。** F4 的"本机是 120Hz 单屏"自此过时；"180Hz 在本机不存在/全矩阵不可能"相关结论随之失效（180Hz 测点重新可用）。对现行已合并最终版 `expand_timing` 的影响：动态读取刷新率，180Hz → interval=6ms（clamp [4,16] 内，行为正确）；冻结的 305 行版 `>90Hz 半刷新率`策略在 180Hz 下会主动降到 ~90fps 内容——又一个不要再用它做体感测试的理由（该 worktree 自 2026-08-23 审查起仅作对照）。
- **审查闭环轮已合并（2026-08-24）**：主工作区生成端修复与 timing 候选以 `6237fee` 提交，并随 Fluent merge `1c220ec` 合入当前产品分支；详见下文「审查闭环（2026-08-24）」与 Git State。
- **用户决定（2026-08-24）：取证全部跳过，转入合并。** PresentMon/60Hz 控制组不再等待；R1/R2/R5 归因永久保持"未知（代码侧分析成立、呈现端未取证）"。补充实机观察（用户口述，非仪器）：**卡顿与是否运行任务无关（空闲也卡）**——按 R4 签名规则，TaskTab 信息表等任务负载型偷帧源排除主因，R4 整体降级；剩余嫌疑为 R1（默认原生路径 16ms 统一时钟在 120Hz 上的固有错拍）/R2（单帧重布局成本）/R5（位移上限），不做进一步分辨。
- ⚠️ **两版 `expand_timing.py` 分歧已按审查决定处理**：`.local-dev/worktrees/expand-native-vsync`（305 行版）维持冻结，仅作对照；其未提交测试改动经逐项审阅**全部为 360ms 调参的等待时间膨胀，无吸收价值**（有行为价值的测试主工作区早已逐字吸收）。
- **合并交接 prompt 已被 Fluent 合并流程吸收执行**：`docs/handoff/ui-expand-merge-codex-prompt.md`。

## 审查闭环（2026-08-24）

按「Codex 独立审查（2026-08-23）」逐条处置；只闭环可代码侧定论的项，证据依赖项保持原状：

| 审查 finding | 处置 |
|---|---|
| CRITICAL：alpha-fix `_env_carrier()` 缺省返回 painter | **已修**。缺省改为 `off`（opt-in：`1/true/on/yes→painter`、`quick/qml→quick`、其余含未设与拼写错误→off）；同步更新 3 处固化旧缺省的测试断言（`test_expand_transition.py::test_env_switch_parsing`、`test_expand_transition_quick.py::test_env_carrier_parsing` / `test_env_config_application_sets_carrier_and_enabled`）与 quest_ui.py 安装注释。该 worktree 60 项 transition 测试全过。 |
| HIGH：timing/transition 双 installer 嵌套 | **已核实无叠装**。主工作区 src/ 内不存在 `expand_transition` 任何形态，产品链仅 `install_expand_timing()`（quest_ui.py:22，default-off）；alpha-fix worktree 仅装 transition。两 worktree 均无双装。无须代码变更。 |
| MEDIUM：`content_height` 终值缺几何签名门禁 | **已实现（代码层）**，见「Current Implementation」。实机回归（resize/DPI/AsNeeded/部分可见）仍待做。 |
| MEDIUM：420ms 时长上限 | **维持待证据**（审查原决定：R1/R2 实机证据后再定产品策略），未动。 |
| LOW：自动化门禁≠呈现验收 | **维持文档口径**：本 handoff 与验收报告继续区分"代码/几何 gate"与"真实 displayed-frame gate"，后者只认 PresentMon/屏幕 probe/用户实机。 |

**R4（动画期偷帧源）审计结论（2026-08-24，只审计未改码）**：`_animation_running` 门禁**只覆盖两处高度写入**（`apply_quest_chrome` 总高写、`quest_adjust_view_size` 总高写），**没有任何周期刷新源查询它**。逐源核实：

- 框架 `TaskTab` 信息表 1s 定时器（`.venv/.../ok/gui/tasks/TaskTab.py:45`，任务运行时每 tick `setText`，与卡片同页同窗口、从不停止）是最强 R4 候选；位于框架包内，不改 `.venv`，若实机证据支持再议包装方案。
- `_CardRefresher` 1s 心跳与 `communicate.task` 事件刷新：脏检查后稳态近零开销，但运行中阶段文本变化仍会 `setText`/seal 重绘（动画期可见）；`apply_quest_chrome` 的 header 高度写在门禁检查**之前**（总高写才被门禁）。
- `LiveScreenshotWidget` 50ms 预览（SmoothTransformation 缩放重绘）在启动页，`hideEvent` 已停，与任务页卡片不共存；`QuestStatusBar` 1s、`DailyBoardBanner` 30s、`AutoLoginStatusTab` 500ms（独立诊断页）均同页或他页低频。
- 分层脆弱点：responsive 版 `_adjustViewSize`/`_onExpandValueChanged` 在运行时是**死代码**（quest_cards 后装整体替换），仅靠 `src/config.py` 安装顺序（responsive 先、quest_ui 后）维持——顺序翻转换回来的是无门禁版本。
- 处置依据审查既定规则：R4 各源未取 displayed-frame 证据前不做行为修改，以上为审计记录。


## Confirmed Findings

### F1（真 bug，已修）收回动画有一半时长是死区 — 源码 + 离屏实测

`responsive_task_config.py` 收回终值原为 `verticalScrollBar().maximum()`，但 `_onExpandValueChanged` 的高度经 `max(..., header_height)` 钳位。实测滚动条需走 441 而真实位移仅 220px：**后一半全程高度不变**。255ms 的动画在 106ms 就停止移动，全部位移压进 7 帧，单帧最大跳 **69px**。

修为 `content_height`（与展开起点对称）后：**7 帧 → 16 帧，单帧最大跳 69px → 43px，运动铺满全程 255ms**。

这正是用户描述的"移动距离长导致目测帧率低"，且**机内计时器全程正常，内部指标看不出来** —— 说明"机内帧率"类指标对本问题无诊断力。

### F2（已修）OutExpo 首帧瞬移 84px — 离屏实测

上一轮采用的 OutExpo 是错误选择：实测首帧即跳 84px，恰是"慢起步"的反面，观感为一次突跳后滑行。换 `(0.4, 0, 0.2, 1)` 三次贝塞尔（Apple/Material 标准曲线）后实测展开帧序列 **2→7→11→19→24→37px**，首帧 0.7px，末段约 6 帧亚像素缓冲落定。

### F3 测量口径限制（必须传递给下一位）

上述 `16 帧 / 43px` 是 **offscreen 平台 4ms 轮询**采得，只能证明"死区消失、曲线形状正确"，**不构成真实呈现帧率的证据**。真实顺滑度的已知事实仍来自 `ui-expand-transition.md`：quick 载体 120Hz 锁相成立（p50≈8.5ms），而**收回方向 2× vsync 顿挫经用户实机确认仍在**。离屏测试对 DWM/表面层问题结构性不可见。

### F4（硬件事实，2026-08-23 实测）本机是 120Hz 单屏，不是 180Hz

平台直查（未开真实窗口）：`S27K1 / 120.000Hz / 2560×1440 / DPR=1.0 / 96dpi`，单一屏幕。

这条推翻了两个此前的书面前提：

1. **305 行版的调参前提在本机不成立。** 其 `_refresh_interval_ms` 为 `period_ms = 2000.0/rate if rate > 90.0`，clamp `[8,17]`。代入 120Hz：`2000/120 = 16.67 → 17ms`，即**内容按 60fps 驱动**。它的 docstring 用"整页重绘装不进 180Hz 的 5.6ms 预算"作理由，但本机预算是 **8.33ms**；注释自述"tuned on a 180 Hz display"，说明调参显示器并非用户当前显示器。**在本机它确定性地放弃一半可达帧率** —— 这给"拒绝 305 行版"提供了实测依据，而非仅论证。
2. **"优先 180Hz，不可用则 120Hz"的采样矩阵无法照做。** 180Hz 在此硬件上不存在，不是优先级问题。可测点只有 120Hz，60Hz 控制组需用户手动切换显示模式。60/120/180Hz 全矩阵在本机**不可能完成**，任何引用该矩阵的 gate 都需改写为 120Hz + 手动 60Hz。

## Remaining Problems（架构根因，按对目测帧率贡献排序；均未实机验证）

**R1 驱动源与 vsync 无相位关系（最高优先）。** `expand_timing.py` 以 QTimer 插值（主工作区版 `_MIN=4/_FALLBACK=8/_MAX=16`）。QTimer 是墙钟定时器，8ms 打在 8.33ms（120Hz）/16.67ms（60Hz）节拍上必然拍频：部分 vsync 间隔内写两次（前一次白写），部分一次不写（重复帧）。观感 = 周期性双倍帧，与实机记录的"收回 2× vsync 顿挫"签名一致。**内部计时器加密不会改善，多出的写入未被呈现。** 这解释"机内帧率再高、目测帧率低"。
→ 注意 `expand-native-vsync` worktree 的半刷新率策略是对同一根因的**另一种**应对（承认单帧预算不足，主动降到半速换均匀），与"提高相位对齐"方向不同，需择一。

**R2 每帧代价是一次布局而非一次绘制。** 当前每帧 `setFixedHeight` → 卡片 relayout → 父滚动区 relayout → 重绘，成本随内容复杂度增长；宽卡/多控件卡易单帧超预算而错过 vsync。丢帧分布不均，比整体低帧率更易被眼睛捕捉。

**R3 单卡展开触发整列回流。** 展开顶动下方全部兄弟卡，父布局每帧重算 N 个兄弟。随任务列表长度恶化；当前规模未必显性，属架构缺陷。

**R4 动画期偷帧源。** 稳态每秒脏检查、live screenshot、run panel 刷新若落在动画期会抢帧。已有 `_animation_running` 门禁，**需核对是否覆盖全部刷新源**（未核对）。

**R5 感知层残留。** 单帧位移峰值仍 43px，上限由 distance/duration 决定，当前 420ms 封顶对超高内容偏紧。

## Proposed Solutions（Claude 原方案，未实施；Codex 审查见下文）

### A. 换驱动源：由呈现节拍驱动插值（治 R1，优先级最高）

每帧取值从"定时器到点就算"改为在呈现回调（`QQuickWindow::frameSwapped` / `beforeRendering`）内按单调时钟经过时间取样，而非累加帧数。

**必须显式承认的平台事实：widget 侧无可靠 vsync 对齐手段** —— `QWindow::requestUpdate()` 的对齐平台相关，Windows 叠加 DWM 合成后不保证锁相；而实机已测到 quick 载体 p50≈8.5ms 锁相成立。**因此本方案实际同时决定了载体：要锁相就得走 QQuickWindow 路线，painter overlay 只能靠定时器逼近。这个取舍不能两头都要。**

### B. 每帧从"重布局"降为"改裁剪"（治 R2/R3）

切换起点将展开后内容渲染为快照（按 devicePixelRatio 出图），动画期只移动裁剪矩形与下方内容整体 y 位移，终点一次性交回真实布局并释放快照。每帧成本变常数，与内容复杂度、列表长度解耦。
这正是 `claude/expand-transition-20260819` 的 painter overlay 已实现的（实测"结构性零白块"）。代价：动画期内容不可交互（300ms 可接受）、高 DPI 需按缩放比出图、快照与真实布局交接帧须对齐像素否则 1px 跳动。
⚠️ 该路线已知 P0 缺陷未解：交接期 DWM 部分更新帧导致收起后 +125ms 陈旧像素带回退（详见 `ui-expand-transition.md` F31），**B 不是干净的现成答案**。

### C. 感知层收尾（须在 A/B 之后）

两条路：放宽长内容的时长上限；或产品侧限制展开距离 —— 内容超过某高度改为"折叠到最大高度 + 内部滚动"，直接压小动画距离。后者同时缓解 R3，**推荐后者**。

## 关键架构风险（本轮最重要的判断）

现存**三套**动画机制，各带 kill-switch，解决同一问题的不同侧面：

| 机制 | 位置 | 状态 |
|---|---|---|
| `expand_timing.py` 定时器重定时原生链 | 主工作区（未提交，266 行）**+** `expand-native-vsync` worktree（未提交，305 行，更演进） | 两版分歧 |
| painter overlay 快照回放 | `claude/expand-transition-20260819` (`5fddfb8`) | P0 未解 |
| quick QQuickWindow 载体 | 同上 | 实验特性 |

**不要再加第四套。** 建议把下一轮定义为**收敛**而非新增：选定一个载体（若要锁相则是 quick），把 `expand_timing` 的插值/终值/门禁逻辑并入该载体，删除落选者，使"谁写高度"始终只有一个答案。

收敛时最易破的坑（CLAUDE.md 已记录）：ExpandSettingCard 高度写入者仅两处且公式必须同步（`apply_quest_chrome` ↔ responsive `_adjustViewSize`），动画期须为唯一写入者。

## Codex 独立审查（2026-08-23）

### 总体结论与五点判断

Claude 对“生成端指标正常而目测仍卡”的问题分层是合理的，但当前证据不足以直接批准 A/B/C 的架构改造。F1/F2 是已经由代码与离屏几何探针支持的生成端修复；R1 是否造成真实 displayed-frame 缺口、以及应选择哪个载体，仍须在 Windows/DWM 实机取证后决定。

| 审查点 | 独立判断 | 结论摘要 |
|---|---|---|
| 1. 平台取舍 | **需要更多证据** | 普通 QWidget 确实没有 Qt 公共 API 提供逐帧 present 回调或可控 swap interval，但“要锁相只能 QQuickWindow”是过度收窄。Windows 侧仍有 `DwmFlush` / `DwmGetCompositionTimingInfo` 等节拍实验路径；OpenGL/DXGI swap interval 则意味着新建专用 surface，实质上仍是另一载体。它们都不是现成、跨平台、无副作用的 QWidget 解法。 |
| 2. R1 是否真凶 | **需要更多证据** | 8ms/6ms QTimer 与 120/180Hz 的拍频是可信假设，不是因果证明。QTimer 可能迟到或合并，QWidget backing-store 写入也可能被 DWM 合并；已确认的“收回 2× vsync”来自 overlay vanish-band/交接路径，不能直接归因 native timer。先取 displayed-frame 证据，再批准载体重构。 |
| 3. `expand_timing.py` 双版本 | **不同意 305 行版作为基线** | 以主工作区 266/267 行版的“读取 `expandAni` 参数 + 整刷新率候选”作为唯一代码基线；不合并固定 InOutSine、360/320ms 与 >90Hz 半刷新率。整刷新率不必然丢帧，半刷新率则确定放弃潜在可达帧率；二者都仍由 QTimer 驱动，并未锁相。 |
| 4. 收敛时机 | **同意停止新增；不同意立即合并/默认启用/删除** | 现在应冻结新增机制并开始证据驱动的收敛，但 painter 有已确认 P0，quick 仍有独立顶层 surface 的首帧/交接风险。暂以 native `expand_timing` 为唯一候选，保持默认关闭；真实 gate 通过后再删除产品分支中的落选实现。 |
| 5. `content_height` 终值 | **有条件同意** | 当前 qfluentwidgets 明确使用 `ScrollBarAlwaysOff`，高度公式为 `header + content_height - bar.value()` 并钳位到 header，因此收回终值应为 `content_height`。外层页面已滚动或卡片部分可见不改变该内部几何。若未来改为 AsNeeded、动画中途 resize/DPI/内容变化，则需中止并重算。 |

### Findings（按严重程度）

#### CRITICAL — painter 载体存在已确认的 DWM 交接 P0，且 alpha-fix 代码当前默认启用

- **依据**：姊妹文档 F31 的实机 flash probe 记录了收起终态稳定后恒定约 `+125ms` 出现展开态陈旧像素带、持续 1–2 个合成 tick；DwmFlush 双屏障、updates 门、同步子树重绘、延迟隐藏等均未消除。与此同时，`expand-transition-alpha-fix/src/ui/expand_transition.py::_env_carrier()` 在环境变量缺省时返回 `painter`，与姊妹文档“默认关闭”的文字口径冲突。
- **触发条件**：将 alpha-fix 的 installer 合入产品安装链，且用户未显式设置 `OK_BD2_EXPAND_TRANSITION=0`；收回后发生 overlay → QWidget backing store 的交接。
- **影响**：未经选择即把已确认的 P0 变成默认用户路径；测试全绿也无法发现该 DWM 层缺陷。
- **修复方向**：P0 未解决前不得默认启用 painter、不得把它作为生产收敛答案。若代码进入共享分支，缺省必须为 off；只有 flash probe 满足“E→C 单调、settle 后零 E 回退”且真实 DPI/刷新率矩阵通过后，才可重评默认值。

#### HIGH — R1 仍是未经 displayed-frame 证据支持的根因假设

- **依据**：主版 `_ExpandTimingDriver` 用 `Qt.PreciseTimer` 和 `QElapsedTimer` 按墙钟取 progress；interval 为 `round(1000 / refreshRate)`。这能解释潜在拍频，但不能证明每次 widget 写入对应何时被 DWM 显示。Qt timer 会迟到/合并，QWidget update/backing-store flush 也会被合并。现有 16 帧/43px 数据来自 offscreen 轮询，仅能证明生成值序列。
- **触发条件**：仅凭 QTimer 与刷新周期的数值接近，就投入 QQuickWindow 迁移、删除原生候选，或宣称“2× vsync 已定位”。
- **影响**：可能用高风险的独立 surface/交接架构替换一个并非主因的计时器，而真正瓶颈仍是 relayout/paint 超预算。
- **修复方向**：先完成下方最小 PresentMon gate；只有 displayed gaps 与 timer 相位漂移相关、且 UI 每帧成本未超预算时，才把 R1 升级为 confirmed finding。

#### HIGH — “只能 QQuickWindow”不是平台硬约束，但其他路径也不是免费第三解

- **依据**：普通 QWidget 没有 `frameSwapped`/`beforeRendering` 等自身 present 回调，也没有 swap interval；`QWindow::requestUpdate()` 只请求更新，不能证明 QWidget HWND 与 DWM 锁相。Windows 可用 `DwmFlush` 阻塞到合成边界，或用 `DwmGetCompositionTimingInfo` 估计节拍；自建 OpenGL/DXGI swap chain 可控 interval，但那已经是新的原生 surface。把插值挂到辅助 QQuickWindow 的回调也只锁住辅助 surface，不能证明 QWidget backing store 同一帧上屏，而且渲染线程回调不能直接安全写 QWidget。
- **触发条件**：把“QWidget 没有公开逐帧 present signal”推导成“必须复用现有 quick overlay”，或反过来把 `DwmFlush` 当成已证明的无阻塞 vsync callback。
- **影响**：错误收窄会忽略可取证的 Windows pacing 实验；错误放宽又会引入第四套机制或在 GUI 线程阻塞事件循环。
- **修复方向**：本轮不新增第三/第四载体。把 native QWidget 路线视为“未证明锁相但可实测”的候选；把 quick 视为已有的实验对照，而不是由平台断言自动胜出。

#### HIGH — 两版 timing 与多个 installer 形成互斥契约，不能同时合并

- **依据**：主版读取 `expandAni.duration()/easingCurve()`，按约一刷新周期驱动；305 行版硬编码 InOutSine、360/320ms，并以 `2000/rate` 主动半速。两版 `_env_enables()` 均以缺省值 `"1"` 启用。`install_expand_timing()` 与 `install_expand_transition()` 各自保存当时的 `ConfigCard.setExpand` 为 previous、只检查自己的 installed marker；若同一安装链依次装入，会形成嵌套 wrapper，kill-switch 不再等价于直达原生。另，305 行版在调用 previous 前设置箭头 duration，但 qfluentwidgets `ExpandButton.setExpand()` 随后会重设为 200ms；只有 easing 残留，runtime disable 也不恢复它。
- **触发条件**：整文件合并 `expand-native-vsync` 的未提交改动，或同时安装 timing 与 transition。
- **影响**：谁拥有 toggle、谁写高度、关闭某个开关后落到哪条链都变得不确定；产品手感与性能契约也不可复现。
- **修复方向**：冻结 305 行版；只把其中测试按行为价值逐项审阅，不整批 cherry-pick。后续仅允许一个外层 installer/一个动画写入者。若证据要求降 cadence，应在同一 266 行基线内形成一个自适应策略，而不是保留第二版本。

#### MEDIUM — `content_height` 终值正确，但正确性依赖当前宽度/滚动策略在动画期稳定

- **依据**：当前 qfluentwidgets `ExpandSettingCard.__initWidget()` 明确设置 vertical/horizontal `ScrollBarAlwaysOff`；responsive `_onExpandValueChanged` 使用 `max(header + content_height - value, header)`。因此 value 到达 `content_height` 恰好落到 header，继续走到 `maximum()` 只会制造死区。外层页面滚动与部分可见只改变裁剪/坐标映射，不改变内部终值。
- **触发条件**：未来改成 `ScrollBarAsNeeded`、滚动条显隐改变 viewport 宽度、动画中途窗口 resize/DPI 迁移、子配置显隐或文本换行改变 `content_height`。
- **影响**：目标值与实际内容高度产生反馈变化，可能终点跳动、提前钳位或中途出现第二高度写入者。
- **修复方向**：保留当前修复；新增的不是另一套动画，而是同一驱动的几何签名门禁：宽度/策略/内容高度变化时中止并按新几何落终态或重算。将 AsNeeded、已滚动页面、部分可见卡、resize/DPI 列入真实 UI 回归。

#### MEDIUM — 420ms 时长上限不能保证长卡的单帧位移预算

- **依据**：`base_duration = min(420, max(280, 240 + 0.28 * content_height))` 在内容高度约 643px 后封顶；更长卡片的平均/峰值位移继续线性增长。离屏测得的 43px 峰值只对应被测卡与生成采样，不能外推到 60Hz displayed frames 或超长卡。
- **触发条件**：内容高度显著超过 643px，尤其 60Hz 或 UI 线程同时有刷新负载。
- **影响**：即使节拍均匀，单个 displayed frame 的位移仍可能过大而显得卡顿。
- **修复方向**：在 R1/R2 实机证据后再定产品策略：继续按距离放宽时长，或采用方案 C 的最大展开高度 + 内部滚动。不要再以提高应用内 tick 数掩盖位移预算。

#### LOW — 现有自动化门禁不构成呈现帧率验收

- **依据**：780 项单测与 offscreen 探针覆盖语义、终值、反向切换和曲线形状，但测试平台没有真实 DWM、swap chain、显示缩放与屏幕扫描输出。
- **触发条件**：用“全测通过”或 distinct height frame 数宣称用户目测问题已经解决。
- **影响**：把生成端正确误报为呈现端完成，导致带 P0 的载体进入默认路径。
- **修复方向**：文档与验收报告必须分开写“代码/几何 gate”和“真实 displayed-frame gate”；后者只接受 PresentMon/屏幕级 probe 与用户实机结果。

### 取证阻塞状态（2026-08-23，接手必读）

**PresentMon 本机不存在。** 全盘搜索零命中：PATH、`Program Files`、`Program Files (x86)`、`Tools`、`Downloads`、`Desktop`、`ProgramData`、仓库内。取证的首选路径因此**未启动**，且不得自行联网安装（需用户提供路径或明确授权）。

**替代方案的判因力弱一档。** 屏幕 BitBlt/ROI hash + QPC probe 能测出"像素何时真的变"，即 2× gap 是否存在；但拿不到 `PresentMode`/`SyncInterval`/`Dropped`，**无法把 gap 归因到 timer 相位**。走这条路 R1 只能停在"未知"，不能升级为 confirmed finding。

**`flash_probe.py` 路径修正**：不在主工作区，实际在 `.local-dev/worktrees/expand-transition-alpha-fix/.local-dev/experiments/expand-guard-verify/flash_probe.py`。

**待用户决策的两项**（在此之前取证无法推进）：① PresentMon 提供/授权，或接受降级到屏幕 probe；② 手动切换显示器到 60Hz 以取得控制组。

### R1 最小充分取证方案

1. **先验证工具覆盖面**：确认 PresentMon 能为目标应用 PID/HWND 产出进程级 present/displayed 事件。普通 QWidget 可能只经 backing-store/Win32 路径更新；若 CSV 没有该进程的有效 displayed 事件，不能把 DWM 自身帧率当成应用帧率，必须改用现有屏幕 BitBlt/ROI hash probe 与 QPC 时间戳。
2. **固定变量**：同一 build、同一卡片与内容、同一窗口几何/DPI、无任务运行；关闭 painter/quick。至少比较：原生基线（timing off）与主工作区 266/267 行 timing（timing on）。
3. **最小决策样本**：本机只有 120Hz（见 F4），在 120Hz 下分别做不少于 20 次展开和 20 次收回，60Hz 控制组需用户手动切换显示模式。**180Hz 在本机不可测，涉及它的全矩阵要求已失效**，勿照抄。
4. **同钟标记**：记录 QPC/`perf_counter_ns` 的 toggle、每次 `_tick`、progress、`_onExpandValueChanged` 前后耗时和终点；PresentMon 保留可用的 `MsBetweenPresents`/`DisplayedTime`、`PresentMode`、`SyncInterval`、`Dropped` 等字段并对齐时间轴。
5. **判因规则**：若 relayout/paint 耗时低于一个 vsync 预算，但多个 timer tick 稳定落在同一 displayed interval、随后出现周期性 2× gap，R1 获支持；若 gap 与 UI 每帧成本超预算对齐，则优先 R2；若 PresentMon 看不到 QWidget 进程级 present，则结论保持未知，用屏幕级 ROI 单调性/重复帧序列补证。

### 第 3 点明确决定：统一到主工作区 266/267 行基线

1. `D:\ok-bd2\src\ui\expand_timing.py` 是唯一后续候选；保留“参数读自 `expandAni`、按 wall-clock progress、终态/反向/sole-writer 门禁”的结构。
2. 不合并 `expand-native-vsync` 的固定 InOutSine、360/320ms、箭头改写和 >90Hz 半刷新率策略；该 worktree 保留作对照，两个未提交测试文件只按有效行为覆盖逐项吸收。
3. “整刷新率”目前只是待实机验证的候选，不是已证明锁相或默认启用的产品契约。若证据证明单帧预算不足，只在同一驱动里调整 cadence/降级规则，并写清触发数据。

### 第 4 点明确决定：现在冻结新增，证据通过后再做物理删除

1. 立即停止新增载体；禁止第四套机制，也禁止同时合并多个 `ConfigCard.setExpand` installer。
2. native `expand_timing` 作为唯一产品候选，PresentMon/屏幕 probe/真实客户端 gate 通过前保持 **default-off**。
3. painter 因 P0 不得生产化；quick 保持实验对照。现阶段不把两者与 timing 一起合进产品安装链，也不急于删除其独立 worktree/探针证据。
4. native 候选通过 60/120/180Hz、DPI、滚动/部分可见、快速反向与 flash-probe gate 后，再从产品分支删除 overlay/quick 的安装代码与落选实现；保留必要诊断产物于 `.local-dev`，不要把三套长期并存于主线。

## Current Implementation（已落地，未提交）

`src/ui/responsive_task_config.py` 内 `responsive_set_expand`（约 407–450 行）：

1. 模块级 `_EXPAND_EASING = _build_expand_easing()`（BezierSpline `(0.4,0)(0.2,1)(1,1)`），每次 toggle 调用 `expandAni.setEasingCurve`。
2. 收回终值 `content_height`（原 `verticalScrollBar().maximum()`）—— 治 F1。
3. `base_duration = min(420, max(280, int(240 + content_height * 0.28)))`，收回取 0.85 倍；复用 `spaceWidget.height()` 避免同一次 toggle 内二次遍历布局。

`src/ui/expand_timing.py` 缺省已改为 **opt-in**（2026-08-23）：`_env_enables()` 从黑名单反转为白名单（缺省值 `"1"` → `""`，判据 `not in {0,false,off,no}` → `in {1,true,on,yes}`）。此前环境变量未设时缺省为 **on**，而 `install_expand_timing()` 已在 `quest_ui.py:22` 的安装链末尾，等于"默认启用未过 gate 的机制"—— 与"证据通过前保持 default-off"的决定直接冲突，现已消除。反转成白名单而非仅改缺省字符串为 `"0"`：后者会让 `OK_BD2_EXPAND_TIMING=garbage` 之类拼写错误静默启用。installer 仍幂等（`_expand_timing_installed`），`_enabled=False` 时 `timing_set_expand` 守卫直接落回未改写的原生链。`tests/test_quest_ui.py` 显式设置该 flag，不依赖缺省值。

### 几何签名门禁（2026-08-24 审查闭环新增）

- **`src/ui/expand_timing.py`**：`_ExpandTimingDriver` 增加 `_geometry_key = (view.width(), spaceWidget.height())`（O(1) 探针，`start()` 捕获、每 `_tick` 比对）。变化即 `_abort_on_geometry_change()`：停止驱动、清 state 阴影、按**当下几何**落终态（展开 → bar=0，handler 活测内容高；收回 → bar=重测 `quest_cards._content_height`），再走 `_apply_terminal_chrome`。不新建动画器，符合"同一驱动内中止/重算"的审查要求。
- **`src/ui/quest_cards.py::quest_adjust_view_size`**（运行时 live 版本，responsive 同名函数是死代码）：缓存失效从"动画期不失效"改为**无条件失效**。理由：漏斗调用是事件驱动（子配置同步/resize/构建），至多让下一帧多走一次 heightForWidth 并重新缓存，逐帧仍命中缓存；而旧的"动画期内容不会变"假设正是审查 MEDIUM 推翻的对象——不失效则 spaceWidget 探针拿到陈旧值，内容中途变化检测不到。总高写入门禁不变。
- 测试：`tests/test_quest_ui.py::ExpandTimingTest` 新增 3 项——`test_mid_drive_resize_lands_recomputed_terminal`（宽度变化，展开+收回双方向）、`test_mid_drive_content_change_lands_recomputed_terminal`（隐藏配置行走漏斗触发）、`test_adjust_view_size_mid_drive_keeps_animation_sole_writer`（同几何重测不写总高）。

**归属提示**：同文件 `setStyle(QApplication.style())` → `unpolish/polish` 那一处**不是本轮改动**，Codex worktree `C:/Users/26294/.codex/worktrees/a343/ok-bd2` 内已存在同样修改，属 Codex 域，勿计入本任务 diff 评价。

测试侧：`tests/test_quest_ui.py` 新增 `ExpandTimingTest`（3 项）。其中 `test_reversal_mid_drive_lands_opposite_terminal` 的注释已随终值修复更新（原注释描述旧的 bar-maximum 终值，已失效）。收回帧数断言在修复前**真实失败**（仅 2 个收缩帧），修的是成因非断言。

## Rejected / Failed Approaches

1. **OutExpo 缓动**（本轮第一版）：首帧瞬移 84px，与"慢起步"诉求相反。证据见 F2。勿重走。
2. **"提高机内计时器频率"作为顺滑度手段**：F1/F3 已证内部计时器正常而目测仍卡；R1 说明未被呈现的写入无价值。**不要用应用内帧率作为验收指标。**
3. `ui-expand-transition.md` 的 4 项已否决方案（半透明保温窗口、show-lowered-then-raise、安装期 warm_up、只修提交闪现）仍然有效，勿重试。

## Relevant Files

- `src/ui/responsive_task_config.py`（本轮改动）、`src/ui/expand_timing.py`（已合并最终版）
- `.local-dev/worktrees/expand-native-vsync/src/ui/expand_timing.py`（**305 行更演进版，未提交**）
- `tests/test_quest_ui.py`（`ExpandTimingTest`）
- 姊妹文档：`docs/handoff/ui-expand-transition.md`（P0 交接闪回、载体细节、flash probe 工具）、`ui-expand-transition-codex-handoff.md`

## Verification

- 提交前门禁（仓库 venv，全局 ok-script 1.0.162 会假失败）：783 项全套 OK（含 3 项新增几何门禁测试）及 `check_change_budget.py`。
- 合并后主工作区门禁（2026-08-24，含仍未提交的其他域测试）：808 项全套 OK、Ruff、compileall、工作区及合并范围空白检查、src 键盘限制扫描。
- alpha-fix worktree：`.venv` 主仓共用，`tests.test_expand_transition` + `tests.test_expand_transition_quick` → 60 项 OK（opt-in 缺省修复后）。
- 帧节奏离屏量法（本轮所用，仅测生成端）：offscreen 平台构造 `_make_batch_stub` 卡片，`QTest.qWait(4)` 轮询 `card.height()` 收集去重帧序列，统计 distinct frames / max jump / 运动结束时刻 vs duration。
- **实机待做**：PresentMon 量**呈现帧率是否≈刷新率**（而非应用内计时器帧率），60/120/180Hz 各测；收回方向单独测确认 2× 顿挫是否消失；高 DPI 快照坐标与纹理；动画中途反向切换；kill-switch 回退。客观验收器 `flash_probe.py`（判据"E→C 单调、settle 后零 E 回退"）见姊妹文档 F33。

## Git State

- 主工作区 `D:\ok-bd2`、分支 `codex/ui-redesign-task-centric`：本任务五文件已提交为 `6237fee`；随后由 merge commit `1c220ec`（父提交 `6237fee` / `7dce7d7`）与 Fluent 动效层收敛。
- 工作区仍混有 **Codex 未提交改动**（map_trade / manual_resolution / main_window_geometry / windows_graphics / config / live_screenshot 等），合并过程未暂存、未覆盖；probe 日志与 shop PNG 也未纳入提交。
- alpha-fix worktree（`5fddfb8` + 未提交改动）2026-08-24 新增未提交修改：`src/ui/expand_transition.py`（缺省 off）、`src/ui/quest_ui.py`（注释）、两测试文件（缺省断言）。该 worktree 另有 Codex 域未提交改动，未触碰。
- 相关 worktree：`expand-native-vsync`（`b76be68`，3 个文件未提交，**维持冻结**）、`expand-transition-alpha-fix`（`5fddfb8`）、`D:\ok-bd2-claude-expand-20260819`（`5fddfb8`）。

## Next Steps

0. ~~取证解阻塞~~ —— **用户已决定跳过全部取证（2026-08-24）**，R1/R2/R5 保持"未知"，第 1 步永久取消。
1. ~~实机 displayed-frame 采集~~ —— 取消（同上）。
2. ~~冻结 305 行版、唯一候选、default-off、不叠装~~ —— 已核实/已落地（见「审查闭环」）。expand-native-vsync 测试审阅完成：无吸收项。
3. ~~核对 R4 刷新源门禁~~ —— 审计完成（见「审查闭环」R4 节）：无周期源查询门禁；用户观察（空闲也卡）进一步将任务负载型偷帧源排除主因。R2 每帧成本记录仍待实机（不阻塞合并）。
4. **合并后实机 smoke（非阻塞、非验收）**：当前工作区含 F1/F2 生成端修复（收回死区 + 新缓动），若用户此前体感来自旧 build，合并后应重新感受一次；顺带覆盖 AlwaysOff/AsNeeded、外层已滚动、部分可见卡、resize/DPI/内容变化中途的几何中止、动画中途反向。
5. ~~合并路径（2026-08-24 定）~~ —— 已完成：native timing 候选以 **default-off** 在 `6237fee` 提交，并随 `1c220ec` 合入产品链；未启用、未迁移载体、未删对照 worktree，呈现 gate 未通过前不重评默认值。

## Warnings / Constraints

- **不要用应用内计时器帧率验收顺滑度**（F1 全程内部指标正常）。离屏测试对 DWM/表面层缺陷结构性不可见。
- 高度写入者仅两处且公式必须同步；动画期须为唯一写入者。
- 不得再引入第四套动画机制。
- 主工作区含 Codex 未提交改动，勿覆盖、勿整体提交。
- 项目硬约束（纯鼠标、禁键盘、相对比例坐标、模板+OCR 门禁）见 `AGENTS.md`；UI 改动走独立 branch + worktree。
