# Claude Code / Fable5 执行 Prompt — 展开动画呈现帧率取证与收敛

> 用法：将本文件正文直接交给 Claude Code 中的 Fable5。权威背景是同目录两份 handoff；源码和 Git 状态若与文档冲突，以当前源码/Git 为准，先报告差异，不要静默采用旧结论。

---

你是 ok-bd2 的 Implementer/Maintainer。请接手“任务卡展开/收回动画目测帧率”任务，目标不是继续堆动画方案，而是先取得 Windows/DWM 真实呈现证据，再把现有三套机制收敛为一个可回退、唯一写高度的产品实现。

## 0. 开始前必须读取并核验

1. 主工作区：`D:\ok-bd2`
2. 必读文档，按顺序完整阅读：
   - `D:\ok-bd2\docs\handoff\ui-expand-transition.md`
   - `D:\ok-bd2\docs\handoff\ui-expand-perceived-framerate.md`，尤其“Codex 独立审查（2026-08-23）”
3. 相关 worktree：
   - native timing 对照：`D:\ok-bd2\.local-dev\worktrees\expand-native-vsync`
   - painter/quick 与 flash probe：`D:\ok-bd2\.local-dev\worktrees\expand-transition-alpha-fix`
4. 先读 `D:\ok-bd2\AGENTS.md` 与 `C:\Users\26294\.ai\engineering-policy.md`，再检查所有相关 worktree 的 branch、HEAD、`git status --short` 和目标文件 diff。
5. 当前 handoff 目录在主工作区是未跟踪内容；不要因为未跟踪而删除、覆盖或遗漏。

## 1. 工作区与权限边界

- 主工作区混有 map_trade、manual_resolution、main_window_geometry、windows_graphics、config、live_screenshot 等用户未提交改动；全部保留，不得回退、覆盖、格式化或顺手清理。
- `D:\ok-bd2\src\ui\expand_timing.py` 当前是 staged A。任何暂存都必须逐文件/逐块选择；禁止 `git add .`、`git add -A` 或整体暂存。
- `expand-native-vsync` 另有未提交的 `tests/test_quest_ui.py` 与 `tests/test_responsive_task_config_ui.py`；先审查行为价值，不得整 worktree 覆盖或整批 cherry-pick。
- 未经用户明确授权，不 commit、push、tag、release、删除 worktree、安装/下载 PresentMon 或改依赖。
- 只使用仓库 venv：`D:\ok-bd2\.venv\Scripts\python.exe`。全局 ok-script 1.0.162 会产生假失败，禁止用全局 Python 作为 gate。

## 2. 已确定的审查结论，不要重新争论或静默改写

1. `content_height` 作为收回终值在当前 qfluentwidgets `ScrollBarAlwaysOff` 契约下正确；外层页面滚动或卡片部分可见不改变内部终值。AsNeeded、resize/DPI、动画中途内容变化需要单独门禁/重算验证。
2. OutExpo 已否决：首帧瞬移 84px。不要重试。
3. offscreen 的 16 帧/43px/首帧 0.7px 只证明生成端死区和曲线形状，不是 DWM 呈现帧率证据。
4. R1（QTimer 与 vsync 拍频）是待证假设；已确认的 overlay 收回 2× vsync/`+125ms` 陈旧像素带不能直接归因 native timer。
5. painter overlay 有实机确认的 DWM 交接 P0，不能默认启用或作为现成产品答案；alpha-fix 当前 `_env_carrier()` 缺省返回 painter，与 handoff 的“默认关闭”历史口径冲突，合并前必须消除。
6. 不引入第四套动画机制。`DwmFlush`/DWM timing 可以作为取证或有界实验，但不得演化为长期第四载体。
7. 第 3 点决定：以主工作区 266/267 行 `expand_timing.py` 为唯一候选基线；不合并 305 行版固定 InOutSine、360/320ms、箭头改写和 >90Hz 半刷新率策略。
8. 第 4 点决定：现在冻结新增并开始收敛，但证据通过前不默认启用、不把多个 installer 合进产品链、不删除仍有对照价值的独立 worktree。

## 3. 第一阶段：先做最小充分的真实呈现取证

### 3.1 预检

- 查找现有 `PresentMon.exe`，记录版本与完整路径；若不存在，停止在“需要用户提供/授权安装 PresentMon”，不要自行联网安装。
- 先确认 PresentMon 是否能对目标 QWidget 应用 PID/HWND 产生有效的进程级 present/displayed 事件。若只能看到 DWM 总体呈现，明确判定“PresentMon 对该 QWidget 路径不充分”，转用现有屏幕 BitBlt/ROI hash probe + QPC 时间戳；不得把 DWM 自身帧率当应用帧率。
- 固定 build、卡片、内容、窗口几何、DPI、主题和任务负载；先关闭 painter/quick。

### 3.2 对照矩阵

至少比较：

1. 原生基线：`OK_BD2_EXPAND_TIMING=0`、`OK_BD2_EXPAND_TRANSITION=0`
2. 主版 timing：只启用 `D:\ok-bd2\src\ui\expand_timing.py` 候选，transition 关闭
3. quick/painter 只作为已有对照，不能借对照结果直接批准生产合并

最小决策样本：用户实际高刷模式（优先 180Hz，不可用则 120Hz）各不少于 20 次展开、20 次收回，另做 60Hz 控制组。若进入产品验收，再补齐 60/120/180Hz 全矩阵，并分别测试空闲与典型任务刷新负载。

### 3.3 同钟仪表与判因

- 用 QPC/`perf_counter_ns` 记录 toggle、每次 `_tick`、progress、bar value、`_onExpandValueChanged` 前后耗时、终点和反向切换。
- PresentMon 保存该版本可用的 displayed/present interval、`PresentMode`、`SyncInterval`、Dropped 等字段与原始 CSV；屏幕 probe 保存 ROI hash/关键翻转帧和 QPC 时间轴。
- R1 只有在“UI 每帧成本低于 vsync 预算，但多个 tick 周期性落入同一 displayed interval，随后出现 2× gap”时才算得到支持。
- 若 gap 与 relayout/paint 超预算对齐，优先判 R2；若工具看不到 QWidget displayed event，结论保持未知并报告覆盖缺口。

产出一张逐候选表：刷新率、方向、样本数、displayed interval p50/p95/max、2× gap 比例、timer tick 数、每 displayed frame 合并写入数、UI 成本 p95、PresentMode、是否支持 R1/R2。

## 4. 第二阶段：依据证据做单一实现，不得预先跳到架构改造

若证据支持继续 native timing：

- 只在主工作区 266/267 行基线上改；保留从 `expandAni` 读取 easing/duration、wall-clock progress、精确终态、反向切换与 sole-writer 门禁。
- 整刷新率只是候选。若实测证明预算不足，在同一驱动内设计有数据阈值的 cadence/降级策略；不要复活第二份固定半刷新率实现。
- 缺省必须 off，直到完整实机 gate 通过。installer 必须幂等，且 `ConfigCard.setExpand` 最外层只有一个本任务 wrapper；关闭开关应直接回到未改写的原生链。
- 高度写入者仅允许 `apply_quest_chrome` 与 responsive `_adjustViewSize` 这两处按同一公式协作；动画运行期驱动者是唯一写入者。先搜索全部 `setFixedHeight`/`setExpand` 包装链再改。
- 对 AsNeeded/resize/DPI/动态内容变化，优先在同一驱动内做几何签名变化后的中止/终态重算，不要创建新动画器。

若证据不支持 native timing 或明确显示其无法达到预算：

- 不要自动切换到 painter 默认启用；其 DWM 交接 P0 尚未解决。
- quick 也只能作为实验候选，必须先说明为何其独立 surface/交接风险可控，并通过同一 flash probe 与 DPI/刷新率矩阵。
- 若现有三者都不过 gate，保持产品原生/default-off，提交证据与下一步建议；不要为了“完成”引入第四套。

## 5. 必须覆盖的行为与实机 gate

- 60/120/180Hz，展开和收回分开统计；典型长卡与短卡。
- 100%/125%/150%（可用时再含 200%）DPI；窗口跨屏/DPI 变化。
- 外层页面顶部与已滚动位置、卡片完全可见与部分可见。
- 当前 AlwaysOff；模拟/兼容 AsNeeded 时验证滚动条显隐不会改变动画目标，不能保证则安全中止。
- 动画中途反向、快速重复点击、滚轮、配置区点击；每次用户点击恰好一次语义 toggle，无穿透。
- 动画期不存在第二高度写入者，终态高度/滚动条值/chrome/箭头与原生语义一致。
- 使用 `.local-dev/experiments/expand-guard-verify/flash_probe.py`：收回必须 E→C 单调，settle 后零 E 回退。
- kill-switch 在运行前与运行中均能安全回到原生；异常静默回退且不破坏 toggle。
- 应用内 timer fps、offscreen distinct frame 数只能作为辅助，不得作为顺滑度验收。

## 6. 已否决方案与禁止事项

- 不重试 OutExpo。
- 不把“提高应用内帧率”当产品验收。
- 不重试 `ui-expand-transition.md` 记录的半透明保温窗口、show-lowered-then-raise、安装期 warm_up、只修提交闪现。
- 不新增第四载体，不让 timing/painter/quick 三个 installer 嵌套。
- 不把 P0 未解的 painter 路线默认启用。
- 不在缺少真实 displayed-frame 证据时宣称 R1 已确认或问题已解决。
- 不评价/回退 `responsive_task_config.py` 中 `setStyle(QApplication.style()) → unpolish/polish`；该改动不属于本任务 diff。

## 7. 验证与交付

迭代时只跑直接相关测试；最终 diff 稳定后按 `D:\ok-bd2\AGENTS.md` 执行一次最终门禁。仓库若有 `scripts/run_checks.ps1`，用其 Final 模式；否则等价执行 Ruff、compileall、仓库 venv 的完整 unittest、`git diff --check` 和 src 键盘限制扫描。仅文档变化可跳过 unittest，但仍要 diff/check_change_budget。

必须交付：

1. 证据报告与原始产物路径（大 CSV/PNG/log 放 `.local-dev`，不要默认纳入 Git）。
2. R1/R2 的明确结论及证据边界。
3. 最终选择的唯一载体/driver、默认值和 kill-switch 语义；若未达 gate，明确保持 default-off。
4. 只列本任务文件的 diff；说明未触碰哪些已有脏改动。
5. 聚焦测试与最终 gate 的准确命令/结果；真实客户端未覆盖项必须明确写“未验证”。
6. 同步更新 `docs/handoff/ui-expand-perceived-framerate.md`，不要新建第四份方案文档。

不要 commit/push/tag/release，除非用户在该会话再次明确授权。
