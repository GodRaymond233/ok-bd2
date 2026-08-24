# 展开动画快照回放层 Handoff（ui-expand-transition）

## Goal

任务卡（ExpandSettingCard）展开/收起动画在真实刷新率（60/120/180Hz）下顺滑：消除原生动画的单帧跳变与收回振荡，同时不破坏 `setExpand` 语义链、滚动与点击交互，且可一键回退原生。属"脚本页面 UI 重构长期例外"授权域（见 `ui-redesign.md` 的 Goal）。

## Current State

- 分支 `claude/expand-transition-20260819`，tip `5fddfb8`，基于 Codex tip `2d7e2af`，**未推送、未合并主线**；独立 worktree `D:\ok-bd2-claude-expand-20260819`。
- **收起后闪回/点击穿透/重复触发修复（2026-08-22，进行中）**：Codex worktree `D:\ok-bd2\.local-dev\worktrees\expand-transition-alpha-fix`（分支 `codex/expand-transition-alpha-fix-20260821`，基 `5fddfb8`）内的后续会话完成根因确认与修复实施（见下方"终态释放输入护栏"节），**未提交、真实客户端场景未实机验证**。
- **架构重构已实施并通过全部验收（2026-08-22 深夜，用户实机确认显示 bug 已解决）**：用户决策"先定向修复（已试，失败）→ 无论结果重构"。新分支 `claude/expand-native-vsync-20260822`（worktree `D:\ok-bd2\.local-dev\worktrees\expand-native-vsync`，基主线 `2d7e2af`，独立 commit 未推送）：`src/ui/expand_timing.py`（~270 行）以刷新率 PreciseTimer（120Hz→8ms，clamp [4,16]）重定时**原生动画链**（bar 中间值 + `_onExpandValueChanged`），真实控件是唯一呈现本体；运行期 `expandAni.state` 实例遮蔽为 Running 保持 sole-writer 门语义；easing/duration 直接读自 `expandAni`（逐帧内容与原生完全一致，只换时钟）；`OK_BD2_EXPAND_TIMING=0` 或 `set_expand_timing_enabled(False)` 一键回退原生。安装于 `install_quest_ui` 末尾。**验收：worktree 764 项测试全过 + Ruff/compileall/diff-check + change budget（+358/-0）；真机 flash probe（9 轮切换）零闪回、零意外翻转、每点击恰好一次 toggle（含 3 次落在旧架构阻塞窗口的快轮）；一键验收工具 `flash_probe.py verify` 双次自测 PASS；用户实机复测确认显示问题解决。**待办：Codex 审阅合并；退役 overlay 分支与相关 worktree；监控脚本（scripts/monitor_ok_bd2.py + 测试，现仅在 codex worktree 未跟踪）是否随迁由 Codex 决策；若 120Hz 顺滑度仍有微调诉求可改 interval 策略（如锁 60Hz=每 2 刷新周期）。
- 定向修复 v2（"遮罩保持可见地重绘子树"）已试败并干净回退：不修闪回（慢轮同签名复现）且引发更糟的头部点击失效（run2 连续 5 次点击无 toggle 无 mousePressEvent）；相关代码已还原到 run1 状态（护栏版）。
- 两载体齐备：painter overlay（`OK_BD2_EXPAND_TRANSITION=1`，**日用推荐**，两阶段 cover，结构性零白块）与 quick QQuickWindow（`=quick`，**实验特性**，已知 1–3 帧占位白）。默认关闭（不设变量 = 原生路径）。
- 该分支上完整套件 804 项全过（仓库 venv；`test_abort_on_external_height_write_mid_quick_replay` 在满载全套下偶发假失败——断言窗口 300ms 小于 cover 预算 350ms，慢 cover 成功即误报，非回归，复跑即绿，后续可把该测试等待放宽）；painter 真机单轮已验证（30 帧/260ms、p50=8.06ms、gap_max=10.66ms 零停顿）。
- **用户实机复测（2026-08-21）**：白块已消失 ✓、启动卡顿已消失 ✓（warm_up 删除生效）、收回顿挫仍在（vanish band 2× vsync，用户决定后续再优化动效）；`b53068f` 修"圆角与方形下拉面板同框"后用户报收回残影+描边增粗 → `5fddfb8` 条带裁剪修复，**用户复测：问题完全没有解决**（根因未确认——离屏探针（DPR 1.0）修复后两方向与原生逐像素一致，复现不了实机症状；疑点：DPR≠1 缩放采样插值 / 整卡残影非 10 行条带所能产生、真凶或在 vanish band。详见同目录 `ui-expand-transition-codex-handoff.md`）。
- 状态：**收回残影+描边增粗未解决（P0），展开圆角修复实机未确认；2026-08-21 已移交 Codex 接手诊断与决策。**
- 原先记录的 `setExpand` 约 70ms 阻塞遗留已由主工作区 `responsive_task_config.py` 的**未提交改动**解决（见 `ui-redesign.md` Warnings）。

## Confirmed Findings

- 原生动画根因（用户 60 帧录屏逐帧 + 离屏复测，已随主线 `2d7e2af` 修复）：`expandAni` 每帧 `valueChanged→setFixedHeight→同步 resizeEvent` 触发 `_adjustViewSize` 与 `apply_quest_chrome`，两者按终态公式立即覆盖动画高度——展开仅 68→1648 两帧，收回在动画值与 68 间振荡 6 次。修法 = 动画运行期间它是唯一高度写入者。
- quick 白块根因一（`53f6931` 修复）：`setTransientParent(主窗口)` 在 Windows 形成 owned-window，破坏 QQuickWindow flip-model 上屏，DWM 持续显示占位填充（场景渲染正确但屏幕脱节）。完整应用探针对照实验实证；修复 = 不设 transientParent，回归 `test_apply_geometry_does_not_make_window_owned` 钉死。
- quick 白块根因二（`e603a0c`/`8b152e7` 修复）：提交先于 overlay 首帧上屏 → 终态闪现 40–150ms；overlay 重开冷呈现 = DWM 占位白 16–50ms（wall−duration≈35ms 实证）。两阶段 `cover()/wait_presented()/play_covered()` 修"提交闪现"；占位色本身 painter 载体结构性规避（viewport 子控件不建原生窗口），quick 载体接受 1–3 帧缺陷。
- PySide6 6.9.1 半透明 QQuickWindow 走 DComp（`CreateSwapChainForComposition`），呈现不可靠且 `grabWindow` 对其读回黑色（QTBUG-135333 区间，Qt 6.10.2 修复）。
- quick 载体 vsync 锁相成立（6819cd4 修复接线后 11 次回放实测）：threaded render loop d3d11 按 120Hz 相位驱动，展开回放 p50≈8.5ms（basic/software 循环是 ~16ms 定时器，不可能出 8.5ms）；裸任务页探针曾跑出 fps=119.6 的完美 120Hz 锁相。收回回放 p50 15–19ms = vanish band 合成层每帧恰错过 8.33ms deadline，稳定掉 2× vsync。
- `QSG_RENDER_TIMING=1` stderr 取证（`e603a0c` 轮）：threaded+D3D11+vsync 8.33ms、315/317 帧 renderer 0ms、blockedForSync≈0、无纹理加载错误——排除 GPU/纹理瓶颈。
- 占位色判定必须用干净终态参照：浅色主题展开态页面本身大面积近白（曾把真实内容误判为 84% 白）。
- 探针环境伪影（非用户问题）：`screen.grabWindow(winId)` 对 D3D flip 窗口读不到交换链内容（须全屏抓取后按几何裁剪）；后台进程跑完整应用时 `viewport.grab()` 下半区黑色。
- **卡片底边 chrome 机制（2026-08-21，`b53068f` 的依据）**：qfluentwidgets 卡片圆角/描边是状态相关的自绘 —— `HeaderSettingCard.paintEvent` 收起态四角全圆（radius 6）、展开态把 header 底角补成直角（`isExpand` 属性在动画启动前即翻转）；`ExpandBorderWidget` 每帧在卡片**当前高度**画 radius-6 圆角描边 + header 下分隔线；EXPAND_SETTING_CARD 的 QSS 为空（body 背景来自 config 控件自身）。因此原生中段帧的边界 chrome == 「带 body 的快照」底部行（离屏探针逐像素证实：native 中段边界行与 after 底部行同图案；before 收起态底部 chrome 图案不同）。快照回放若只按高度方切 after，动画期卡底无圆角无描边 → 用户看到的"圆角卡 + 方形下拉面板同框"。
- **终态释放点击穿透根因（2026-08-22，`logs/monitor/ok-bd2-light-20260822-201804` 全 5 轮实证）**：收起点击落在展开回放终态 `_tick` 内同步阻塞的 `_release_replay_overlay`（实测 313–531ms）期间，原生鼠标消息进入线程队列排队；`_tick` 返回后，**Qt 0ms 零定时器在事件循环 posted-events 阶段先于 Windows 消息泵派发原生输入**——5/5 轮 `_finish_pending_release` 的 compositor flush（如 29.080）均先于排队点击派发（`_set_expand_entry` 29.115，点击→toggle 延迟 110–375ms）。即前一轮"0ms 定时器延迟释放"输入护栏从未真正拦截到点击，全部穿透到真实控件：收起场景下真实布局已收拢、兄弟卡上移，排队点击落在与用户所见动画不对应的控件上 → 穿透、重复 toggle、收起后闪回。修复 = 释放前用 `GetQueueStatus(QS_MOUSEBUTTON)` 检查本 GUI 线程原生队列，仍有按键消息则遮罩保持可见并重启 0ms 定时器（排队点击随后派发到可见遮罩 → `mousePressEvent` 终止路径：头部重投递/其余吞掉），200ms 预算（锚定在阻塞重绘**之后**——重绘本身可 >500ms 不占预算）兜底病理队列；`abort()`/新播放/异常路径 `force=True` 立即完成。监控已补 `mousePressEvent`/`_begin_pending_release`/`_finish_pending_release` pattern 与 `_release_pending`/`force` 字段。离线：`tests.test_expand_transition` 40 项 + quick 20 项 + monitor 6 项全过（新增 4 项护栏回归，含事件循环级"点击与清理同时排队"顺序测试）。
- **收起闪回根因已定位（2026-08-22 夜，flash probe 实测，`logs/monitor/flash-probe-run1` + `.local-dev/experiments/expand-guard-verify/out/run1/`）**：闪回**与输入无关**（慢速轮 3 秒间隔、无排队点击同样复现），是**交接期 DWM 呈现的"部分更新帧"**：屏幕级 38Hz 采样 + 展开态/收起态双参考打分显示，每次收起都是同一模式——收起画面全屏稳定后**恒定 +125ms**，窗口中部带（y≈359–591，正是展开卡配置行区域）**倒回收起前的展开像素**（该带 diff vs 展开参考仅 3–5，与展开参考几乎逐像素一致），16–31ms（1–2 个合成 tick）后恢复全收起；每次闪回帧的各带 diff 分数完全相同 = 确定性同一幅混合帧。时刻精确对齐 collapse 回放终帧（progress→1.0 + terminal paint + DwmFlush + `_begin_pending_release` 的 515–547ms 同步子树重绘窗口）。定性：**遮罩→真实控件的交接在 Qt widget 后备存储/DWM 表面上不是原子操作**——已尝试的 DwmFlush 双屏障、updates 门、逐子控件同步重绘、终态快照重显、延迟隐藏都无法阻止陈旧像素带被重新呈现（本轮还确认 DPR=1.0，DPR 插值假设排除；机器 2560×1440@100%）。2026-08-21 的 P0"收回残影+描边增粗"应属同一现象族。离屏测试结构性看不见此问题（无 DWM/表面层）。
- **输入护栏实机验证通过（同上 flash-probe run）**：3 次快轮（第二次点击落在终态阻塞期内）全部由遮罩 `mousePressEvent` 接收（687–704ms，含强制完成 + 同步重投递 + 整个收起回放启动在点击处理内完成），慢轮的新鲜点击正常直达真实标题。输入穿透问题已修复；剩余问题仅为上述视觉交接缺陷。
- **flash probe 工具（可复用的客观验收器）**：`.local-dev/experiments/expand-guard-verify/flash_probe.py`——屏幕 BitBlt 采样（~38Hz）+ QPC 时间戳与监控 trace 同基合并 + 双参考逐带打分 + 翻转帧 PNG 落盘；`snapshot`/`drive` 两模式，安全注入（仅当目标点下是 app 进程窗口才点击；注意 `_APP_PID` 校验 bug 已修——曾拿探针自身 PID 比较）。对任何展开/收起新实现，"E→C 单调、settle 后零 E 回退"即客观通过判据。导航路径：启动后先点侧边栏"日常/周常"（client y≈219，侧边栏项间距 40px、首项 y≈138），一键完成日常卡头部点击点 client≈(300,209)。

## Current Implementation

- `src/ui/expand_transition.py`（painter 载体 ~700 行）+ `src/ui/expand_transition_quick.py`（quick 载体 ~500 行）；安装于 `quest_ui.py` install 序列末尾，包装最外层 `ConfigCard.setExpand`。
- 架构 = 状态/表现解耦：语义 toggle 走无动画提交接缝同回合落位（实例级遮蔽 `expandAni.start`、滚动条终值、`_onExpandValueChanged`、quest chrome 定桩、头部箭头/子开关 120ms 微动画 settle）；视觉由 before/after 双快照在 overlay 回放（PreciseTimer，OutCubic，展开 260ms/收回 220ms，间隔 clamp [4,16]ms）。
- 骑边 chrome 条带（`b53068f` 引入、`5fddfb8` 裁剪，两载体两方向通用）：`_EDGE_STRIP_ROWS=10` 行卡底 chrome（侧缘+圆角弧+淡出描边）随动画边界滑动；条带来源 = **带 body 的快照**底部行（展开取 after、收回取 before）；**只画在卡体区 `[card_top+min(from_h,to_h), h_t]`**、被裁顶时锚定底部（末期/早期帧不越界盖住收起卡/header——否则残影+双描边）；painter 在 `compose` 的 `_draw_edge_strip` 裁剪 blit，quick 在 `play_covered` 用 `QPixmap.copy` 预裁剪经 image provider（id `edge`）+ QML `edgeBand`（clip+底锚，声明于 vanishBand 之后、tailBand 之前）。两端（p=0/p=1）条带自然消失或落回源位置 → 端点仍与快照逐像素一致。
- 两阶段 API 两载体皆有：before 快照先遮盖（painter 经 `repaint()` 同步入主窗体后备缓冲；quick 等 `frameSwapped` 真实上屏，超时 350ms——需覆盖冷首点 ~250-300ms 的引擎+D3D+首呈现）→ 遮盖下提交并抓 after → 无缝切入动画。painter 的 `_grab` 抓取期间临时隐藏 overlay 并立即恢复（无事件处理间隙）。
- 安全边界：单 overlay（二次操作先终止再重启）；overlay 吸收滚轮与非头部点击，头部左键终止后向真实 `HeaderSettingCard` 重投递；每 tick 签名中止（viewport 尺寸/页面滚动/卡高/可见性/最小化/主窗口移动）+ `themeChanged` 即时中止；feature-detect（安装期契约探测 + 每次播放前资格检查）；任何回放异常记一条日志并本会话熔断，语义 toggle 由原生链兜底；kill-switch。回退链 quick→painter→原生。
- 内建仪表：每轮回放自动记一条 INFO（载体/帧数/有效 fps/间隔 p50·max/屏幕刷新率/后端）；`last_replay_stats()`、`dump_diagnostics()`；`OK_BD2_EXPAND_TRANSITION_STATS=0` 关采样。
- 测试：`tests/test_expand_transition.py`（23 项）、`tests/test_expand_transition_quick.py`（19 项，offscreen 钉 software+basic）——终态几何/滚动条值与原生逐项相等、after 快照与稳定后渲染逐像素相等、帧渐进单调、骑边条带=体快照底部行（两方向）、quick edgeBand 跟随边界、各类中止、熔断、kill-switch、输入不穿透、提交在遮盖之下（两载体）、after 快照排除遮盖、窗口跨回放复用等。

## Rejected / Failed Approaches

1. **半透明常驻保温窗口**（防 overlay 重开冷呈现）：PySide6 6.9.1 DComp 呈现不可靠——off-screen 移动后陈旧帧钉死原位 / 第二轮起呈现冻结 / 无输入透明 flag 时整程不上屏（探针 probe_app6–12 + Qt 源码 `qsgthreadedrenderloop.cpp`/`qrhid3d11.cpp` 溯源）。PySide6 ≥6.10.2 后才可重评。
2. **show-lowered-then-raise 预热**：被压底窗口首帧为空场景+白色清屏，raise 后场景闲置不重绘，真机探针 84–92% 纯白（probe_app13）。
3. **安装期 warm_up() 预热**（`e603a0c` 引入、`8b152e7` 删除）：8×8 全离屏从未真实渲染过一帧，纯启动卡顿源。
4. **只修"提交闪现"不修"占位色"**（`wait_presented` 单独使用）：挡不住 show 与首帧呈现间的 DWM 占位白——所以 painter 最终方案是干脆不做顶层原生窗口。

## Remaining Problems

- quick 载体 1–3 帧占位白：用户实机复测已消失（2026-08-21）；随机型/驱动可能复现，仍标实验特性（PySide6 ≥6.10.2 再评估）。
- **收回方向掉 2× vsync（vanish band 每帧错过 deadline）：用户实机确认仍在，用户决定留待后续动效轮**（band 纹理裁剪 / 异步 image provider 预案未实施；动效参数微调候选见 `entry-flash-and-warmup-plan.md` 阶段 2）。
- painter 载体入口 34–44ms（双快照 grab 为主）；悬停预取 before 为后备优化，未实施。
- **收回残影+描边增粗：`b53068f`/`5fddfb8` 两轮修复后用户实测仍未解决（P0，已移交 Codex）**；展开圆角同框修复实机亦未确认。疑点：DPR≠1（探针全程 DPR 1.0，实机缩放下 overlay painter 带缩放变换 → 条带/vanish band blit 分数像素插值，与"半透明重影+1px 线变 2px"症状签名吻合；实机 `QT_SCALE_FACTOR=1` 可快速区分）、整卡残影非 10 行条带所能产生（真凶或在收回 vanish band 合成）。
- 合并主线、默认启用与否 = Codex 决策；建议把三条升级核对（`expandAni` 属性仍在 / `ConfigCard.setExpand` 包装链完整 / `TaskCard.__init__` 包装链完整）增补进 AGENTS.md 的 ok-script bump 流程——任一失效时回放层自动静默回退，仅需人工评估重新适配。
- 实机验证待做项（除已确认的白块✓/启动卡顿✓/圆角修复待复核）：60/120/180Hz 回放顺滑度与节拍保持率、PresentMon 验证 quick 呈现帧率≈刷新率（任务运行 OCR 抢 GIL 时尤其）、高 DPI 的 QWindow native 坐标与纹理清晰度、焦点/任务栏/Z 序、与 qfluentwidgets 悬浮滚动条/触摸滚动的叠放边缘、页面已滚动与部分可见卡、二次点击/滚轮/配置区点击交互正确性、kill-switch 一键回退与 quick→painter 自动降级。
- **终态释放输入护栏（2026-08-22）待实机验证**：真实客户端快速展开→收起（第二次点击落在终态释放阻塞期内）复测，验收 = 新监控里该点击产生 `mousePressEvent` 调用（`self._release_pending=true`）而非直接 `_set_expand_entry`，且每轮恰好一次 toggle、无穿透/闪回。安全验证 harness 已备：`D:\ok-bd2\.local-dev\worktrees\expand-transition-alpha-fix\.local-dev\experiments\expand-guard-verify\run.py`（真实 windows 平台 + SendInput 注入 + 注入前校验落点是本进程窗口否则跳过 + 预热轮 + 完毕自动退出；勿在用户正在用机时运行——2026-08-22 首次无校验版本曾把 6 次左右注入点击打进用户当时置顶的其他应用窗口）。护栏只覆盖 painter 载体 `ExpandTransitionPlayer`；`ScrollTransitionPlayer`（滚轮回放终态同步 abort，同样存在穿透窗口）与 quick 载体 `_finish_handoff` 未改，属残留风险。

## Relevant Files

- 源码：`src/ui/expand_transition.py`、`src/ui/expand_transition_quick.py`、`src/ui/quest_ui.py`（install 序列末尾）
- 测试：`tests/test_expand_transition.py`、`tests/test_expand_transition_quick.py`
- 设计/评审/报告/探针：`.local-dev/experiments/ui-anim-diagnosis-20260818/`（含 `expand-animation-plan-kimi-k3-review.md`）、`.local-dev/experiments/ui-expand-transition-20260819/`（阶段 0/2 报告、`high-refresh-tier2-plan.md`、`entry-flash-and-warmup-plan.md`、`probe_real_quick.py`、`probe_real_app.py`、probe_app1–13）
- 性能基准（UI 稳态）：`.local-dev/experiments/ui-perf-20260818/bench.py`

## Verification

- 门禁（仓库 venv `.venv/Scripts/python.exe`，全局 ok-script 1.0.162 会假失败）：`unittest discover -s tests -q`（该分支 804 项）+ Ruff + compileall + `git diff --check` + src 键盘扫描。
- 实机：设 `OK_BD2_EXPAND_TRANSITION=1`（painter）或 `=quick`，看日志统计行（自带 carrier=）；`QSG_INFO=1`（render loop/后端）、`QSG_RENDER_TIMING=1`（逐帧 sync/render/swap）；呈现帧率用 PresentMon 复核（未做）。
- 排查口径：日志 carrier=painter 而 120/180Hz 屏 → quick 未启用或已降级（查 `dump_diagnostics()`）；carrier=quick 且 gap_p50≈16ms → threaded loop 未启用（QSG_INFO 确认）；carrier=quick 且 p50≈8/5.5ms 但目测顿 → 生成端达标、问题在呈现端（PresentMon）。

## Git State

- 分支 `claude/expand-transition-20260819`（未推送），提交链 `c6a0b6c`→`e26d245`→`c93fe12`→`6819cd4`→`53f6931`→`e603a0c`→`8b152e7`→`b53068f`→`5fddfb8`，基 `2d7e2af`（Codex `codex/ui-redesign-task-centric` tip）。
- 独立 worktree `D:\ok-bd2-claude-expand-20260819`；最后已验证版本 `5fddfb8`（804 项测试 + 离屏逐像素 chrome 对照探针）。worktree 内仅一个未跟踪文件 `qsg_stderr.txt`（用户实机跑 QSG_INFO/QSG_RENDER_TIMING 的 stderr 捕获，诊断产物，勿提交也勿删除）。
- 主工作区（D:\ok-bd2）现有 Codex 未提交改动（map_trade / manual_resolution / main_window_geometry 等），合并本分支时不得覆盖。

## Next Steps

1. **（已移交 Codex，2026-08-21）收回残影+描边增粗诊断**：优先区分 DPI 假设（实机 `QT_SCALE_FACTOR=1` 复测 / 问用户显示缩放比例），再实机中段截帧（改造 `probe_real_app_painter.py`，painter 载体可全屏抓取后裁剪）。
2. 收回 2× vsync 的动效轮（用户已排期"后续再优化动效"）：vanish band 纹理裁剪 / 异步 image provider / 动效参数微调。
3. PresentMon 验证 quick 呈现帧率≈刷新率（尤其任务运行 OCR 抢 GIL 时）。
4. 视结果与 Codex 决策：默认值、合并方式、AGENTS.md bump 核对三条。
5. 若入口 34–44ms 体感差 → 实施悬停预取 before 快照。

## Warnings / Constraints

- 三个探针钉死的坑：QQmlComponent 必须与根项同寿命（其销毁连带删除创建上下文与根项）；requestPixmap 的 id 含 `?n=` 查询串；尾部条带容器必须 clip（否则内层 before 整幅覆盖 after 基底）。
- 任何失败必须静默回退原生（feature-detect/资格检查/会话熔断链），不得让语义 toggle 依赖回放层成功。
- AsNeeded 滚动条页面（提交前预测翻转型）走原生动画——当前 ok-script 页是 AlwaysOff overlay 滚动条（viewport 宽度不因展开变化）；bump ok-script 后若滚动策略变化需重评。
- `responsive_task_config.py` 的 setExpand 优化属 Codex 域（主工作区未提交改动），勿在回放层内绕修。
- 项目硬约束（纯鼠标、禁键盘、相对比例坐标、模板+OCR 门禁）见 AGENTS.md；UI 改动照旧走独立 branch + worktree。
