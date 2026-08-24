# Fluent 动效层 Handoff（ui-fluent-motion）

## Goal

复刻用户点名的 Windows Terminal 设置界面与 Microsoft Store 动效（2026-08-24 需求）：主窗口导航切页过渡 + 日常/周常任务板首 show 错落入场。属"脚本页面 UI 重构长期例外"授权域（同 `ui-redesign.md` 的 Goal）。

第二轮追加（2026-08-24 晚，用户三点反馈）：首页列表选中条滑动、展开动画恒速、隐藏维护配置行（阈值/等待秒数/测试）。

## Current State

- **用户实机确认（2026-08-24 深夜）**：切页残影**完全修复**；下拉（展开）帧数问题**用户决定关闭、不再追**（刷新率驱动实现保留在分支中作为既成能力，`expand_timing` 维持 opt-in 不转默认）。
- **已合并（2026-08-24）**：主工作区先以 `6237fee` 提交展开动画 pending 五文件，再以 merge commit `1c220ec`（父提交 `6237fee` / `7dce7d7`）把 Fluent 五轮并入 `codex/ui-redesign-task-centric`；未 push / tag / release。
- **独立评审闭环轮 `7dce7d7`**：全新上下文评审 agent + 作者复审，3 MEDIUM + 5 LOW 全部修复（详见下文「评审闭环」节）；分支树 792 项全套 + 门禁通过。**行为变化提示：任务板首次进入现为内容错落入场（替代整页升起），后续切换照常整页过渡**——仍待用户下次非阻塞实机 smoke。
- **来源分支与 worktree 保留**：`claude/fluent-motion-20260824` 仍指向 `7dce7d7`，提交链 `8bfa8c1` → `42300e7` → `17a9dd1` → `9d3a6cd` → `7dce7d7`，worktree `D:\ok-bd2-claude-fluent-20260824` 未清理。
- **合并交接 prompt 已执行**：`docs/handoff/ui-fluent-motion-merge-codex-prompt.md`。
- **用户显示器 2026-08-24 起为 180Hz**。
- **与主工作区 pending 的收敛**：本分支现含 pending 五文件中的四份逐字/逐字节一致内容——`expand_timing.py`（逐字节）、`responsive_task_config.py` 的 `responsive_set_expand`（逐字）、`quest_cards.py` 的 `quest_adjust_view_size` 缓存无条件失效（逐字）、`quest_ui.py` 接线；`tests/test_quest_ui.py` 的 `ExpandTimingTest` 6 项也已移植。

## 评审闭环（2026-08-24，`7dce7d7`）

全新上下文评审 agent（对照 ok-script 1.0.190 / qfluentwidgets 实源码 + 三个断言离屏实证）+ 作者复审合并结论，无 CRITICAL/HIGH：

1. **MEDIUM 错落入场生产不可达**：任务板永不为启动页签（`MainWindow.py:91` start_tab 首位、无上次页签恢复），首 Show 必经导航切换 → 被让位规则跳过，"Store 式错落"从未真正播放过。修复 = pending 机制：`_install_first_show_hook` 预置 `_fluent_entrance_pending`，manager 见 pending 页签跳过整页过渡，首 Show 的延迟回调消费 pending 并跑错落；后续切换照常过渡。
2. **MEDIUM kill-switch 中止错落不归位**（agent 离屏实证：中止后 banner/card 偏离 4–28px）：`abort(land=True)` 逐项 `driver.finish()` 落捕获目标 + `layout.invalidate()+activate()` 强制权威几何；布局驱动中止路径保持就地停（布局已给新几何，finish 会写陈旧目标）。
3. **MEDIUM 死对象 RuntimeError 逃逸**（agent 实证：`refresh_ui` 销毁卡片后 kill-switch 在 `driver.stop()` 抛异常，中断全部清理循环）：abort 循环、kill-switch 三类注册表循环、`manager.finish_active`、`_RefreshDriver.finish` 的 timer 访问全部逐项 try 守卫。
4. **LOW Move-only 重排漏网**（RunPanel 显隐/refresh_ui 增删/间距变化均不触发 Resize）：过滤器增挂 `LayoutRequest`。
5. **LOW 两处 `singleShot(0)` 延迟回调**在接收者销毁后触发（Qt 打回溯噪音）：闭包内 try 守卫。
6. **LOW env 开关仅成功路径求值**：改模块导入时求值。
7. **LOW 滚动跟踪依赖未文档化行为**（viewport 只收 Paint，实际靠 blit 滚动移动子控件）：增挂 `verticalScrollBar().valueChanged` 吸附。
8. **LOW 字符串 sub_configs 规则未剥离**（框架支持裸字符串规则，仓库现无实例）+ **INFO 无匹配时不必要的 None→{} 变更**：hide_config_rows 两处修正（无匹配现在完全不触碰任务对象）。

评审同时显式确认干净：切换/选中两条生产动效全生命周期无泄漏、无高度写入、无快照、稳态零成本、配置值零变更、token 审计无误伤、`_on_current_changed` 全守卫。

## Confirmed Findings（实现依据）

- 框架在 `ok/gui/MainWindow.py:56` 显式 `stackedWidget.setAnimationEnabled(False)`——切页瞬切是框架刻意行为，Fluent 观感缺失的直接原因。本层不重新启用 qfluentwidgets 内建 pop 动画（那是 deltaY=76 的无淡入滑入，非 Terminal 观感），而是挂钩 `stackedWidget.currentChanged`（qfluentwidgets `StackedWidget` 逐层转发的信号，覆盖 nav 点击 / `switchTo` / 任何切换路径）。
- Windows Terminal 设置界面（源码核实，`microsoft/terminal` `MainPage.xaml`）：内容 Frame 用 `DrillInNavigationTransitionInfo`（钻入式：新页 0.94→1.0 缩放淡入 333ms，旧页 100ms 淡出）。**Qt 对整页做缩放只能走快照**（QWidget 无 scale 变换），而快照交接正是本机已确认的 DWM 部分更新残影雷区（`ui-expand-transition.md` F31）——故不复刻缩放，改用等效观感的真控件方案：新页自下方升起+淡入（300ms，WinUI 入场缓动 `(0.1,0.9,0.2,1)`），**旧页垫在新页之下保持可见**，新页淡入即视觉等效旧页淡出（且输入始终落在新页=正确目标，无旧页遮输入问题）。
- 动效参数依据：WinUI motion 时序分层（标准时长 300ms；入场 decelerate `(0.1,0.9,0.2,1)`、退场 accelerate `(0.7,0,1,0.5)`）；qfluentwidgets 1.11.2 自带的 WinUI 仿制过渡组件（`EntranceTransitionStackedWidget`/`DrillInTransitionStackedWidget`，同为快照式）印证了这些常数。
- 任务板错落入场只在该 tab **首次 show** 触发（WinUI 语义：导航切页由切页过渡负责，列表入场只在首次加载跑）；若首 show 恰由切页过渡覆盖（`_fluent_transitioned` 标记），错落入场让位，避免双层动画。showEvent 先于 currentChanged 触发，因此错落入场的启动经 `singleShot(0)` 延迟到 currentChanged 之后判定标记；Qt 事件序（LayoutRequest → 零定时器 → 低优先级绘制）保证判定发生在首帧绘制之前，无闪烁。

## Current Implementation

- `src/ui/fluent_motion.py`：
  - `_PageTransitionManager`：挂钩 currentChanged；窗口不可见（启动期切换）不动画；仅跟踪与清理，永不阻止切换本身。
  - `_PageTransition`：新页 pos(0, offset)→(0,0) + `QGraphicsOpacityEffect` 0→1 并行动画；offset = 页高 8% clamp [40,110]。结束时摘除效果、归位 (0,0)、隐藏旧页——**稳态与改动前逐像素一致，零常驻开销**。被新切换/kill-switch 打断时 `finish_now()` 立即落终态。
  - `_StaggerRun`：横幅→卡片→页脚按 35ms 阶梯（上限 8 级）升起 28px+淡入 280ms；**容器或任一成员 Resize 即中止并按当下布局落位**（只挂 Resize 不挂 Move——动画自身在写 pos，挂 Move 会自杀触发）。中止经 `singleShot(0)` 延迟，避免在布局回调内重入绘制。
  - kill-switch：`OK_BD2_FLUENT_MOTION ∈ {0,false,off,no}` 关闭（默认开启，用户明确要的功能）；`set_fluent_motion_enabled(False)` 运行期关闭并落终态所有进行中动效。
  - 稳态零开销不变量：所有 QGraphicsOpacityEffect 在动画结束/中止时 `setGraphicsEffect(None)` 摘除（有回归钉死）。
- 接线：`src/ui/quest_ui.py` 末尾 `install_fluent_tab_entrance()`（wrap `OneTimeTaskTab.__init__`，仅 `日常/周常` 组，首 show 一次性事件过滤器）；`src/globals.py` `on_show_main_window` 末尾 `install_fluent_page_transition(main_window)`（此时窗口尚未 show，天然跳过启动期切换）。
- 不碰展开动画：不写任何高度、不触碰 `expandAni`/`_adjustViewSize`/`apply_quest_chrome` 链——不违反"不得引入第四套展开动画机制"（2026-08-23 审查决定）。

### 第四轮（9d3a6cd，用户实机反馈闭环）

- **`_RefreshDriver`**：所有动效（切页 pos+opacity、错落入场逐项、选中条 geometry）由 PreciseTimer 按屏幕刷新率间隔采样缓动进度（`round(1000/rate)` clamp [4,16]，与 expand_timing 同法），不再走 QPropertyAnimation/Qt 统一时钟。首帧同步采样；`finish()` 同步落终帧；`stop()` 不落帧（错落中止路径用，布局已给新几何）。
- **切页无叠底**：`_PageTransition(incoming)` 只动新页；旧页由 QStackedWidget 在 `setCurrentIndex` 原生隐藏。任何时刻不存在旧页可见窗口（两轮反馈证明"叠底+淡出"无论怎么调都读作残影）。
- **expand_timing 移植**：主工作区最终版逐字节复制（opt-in 白名单 + 几何签名中止 + trace），`quest_ui` 接线与 pending 一致；随附移植 `quest_cards.quest_adjust_view_size` 缓存无条件失效（ExpandTimingTest 的内容变化用例依赖它）与 6 项测试。用户测试指南：`OK_BD2_EXPAND_TIMING=1` 开启展开动画刷新率驱动做 A/B。

### 第二轮（42300e7）

- **首页选中条滑动**（`fluent_motion.py` `_SlidingSelection`）：qfluentwidgets `ListWidget` 的选中蓝色条由 `ListItemDelegate._drawIndicator` 静态绘制（选中行半透明背景仍在 delegate）。本层在 viewport 上放一个 3px 圆角主题色指示条控件（几何与 delegate 逐像素一致：宽 3、圆角 1.5、纵向内缩 0.257 行高），`itemSelectionChanged` 时 200ms 入场缓动滑动；Scroll/Resize 即时吸附。接管方式 = delegate 实例属性遮蔽 `_drawIndicator`（paint 全程 Python，实例遮蔽生效）；kill-switch `del` 遮蔽即完整还原原生。安装于 `install_start_list_motion(main_window.start_tab)`（globals 接线），覆盖 device/capture/interaction 三个列表。
- **展开恒速**（`responsive_task_config.py`）：复刻主工作区待合并的 `responsive_set_expand`（`240+0.28·content_height` clamp [280,420]、收回 0.85×、BezierSpline (0.4,0)(0.2,1)、收回终值 `content_height`、unpolish/polish）。修复"展开速度随栏目数量变化"（原基线固定时长+可变距离=速度不恒定；距离缩放后速度恒定，配合隐藏维护行后大多数卡落在 ~280ms 下限）。
- **隐藏维护配置行**（`src/ui/hide_config_rows.py`）：token = **阈值/秒数/分钟/像素/命中/次数/测试**（子串匹配；秒数涵盖等待/确认/间隔/宽限秒数，次数涵盖重试/滚轮/最多点击次数——`42300e7` 首版仅 阈值/等待秒数/测试，`17a9dd1` 按用户追加点名扩全，经全量键名枚举验证 96 个维护键 100% 覆盖且行动类配置零误伤）。走框架官方通道 `config_type[key]['hidden']`（`ConfigCard.__initWidget` 顶层循环创建期跳过）；**并从所有 `sub_configs` 规则中剥离 token 键**——框架的子配置递归创建路径不查 hidden，剥离后键回落到顶层循环即被 hidden 拦截（快速狩猎"执行快速狩猎"的阈值/测试子键正是此路径）。wrap `TaskCard.__init__` 在原始 init 前注入；配置值与持久化完全不动，改 `HIDDEN_CONFIG_TOKENS` 重启生效。
- **切页旧页真实淡出**（`17a9dd1`，修用户实机报告"旧栏目任务残留一小会与新栏目重叠"）：首版旧页全程不透明垫底，新页淡入期两页文字叠印、新页升起露出的顶部条带显示旧页内容。现改为旧页同步加速淡出（150ms `(0.7,0,1,0.5)` WinUI 退场曲线），互补双向淡出使任一时刻只有一页完全可读；旧页仍垫在新页之下，输入始终落新页不变。

## Rejected / Failed Approaches

1. **复用/启用 qfluentwidgets 内建过渡**（PopUp 动画 deltaY=76 无淡入；Entrance/DrillIn 两套均为快照式，DrillIn 整页缩放必须快照）——快照交接在本机有已确认的 DWM 残影前科，弃。
2. **旧页盖顶淡出**（等效 Terminal DrillIn 旧页退场的另一种实现）：旧页置顶期间拦截鼠标输入 100ms，需整树 `WA_TransparentForMouseEvents`；改为旧页垫底方案后输入天然落在新页，零 hack。
3. **错落入场在每次切到任务板都重播**：WinUI 语义是导航过渡负责切页、入场只在首载；每次重播在工具型应用里显得拖沓。

## Remaining Problems

- **仅 `7dce7d7` 的首次任务板错落入场尚未实机目测**；`9d3a6cd` 的切页残影修复与动效体感已由用户在 180Hz 实机确认。离屏帧序列仍只证明几何/透明度序列正确，不替代后续对新 pending 路径的非阻塞 smoke。
- 参数调优空间（用户实机反馈后再动）：切页 offset 比例（现 8%）、时长（300ms）、阶梯密度（35ms/8 级上限）、选中条滑动时长（200ms）。
- 悬停微交互（Store 卡片 hover 抬升）未做——属独立小改动，等用户看过本轮体感再定。
- 隐藏维护行后若某张卡全部行都被隐藏，会变成"空展开区"（当前任务卡均有非 token 行，未出现；若未来出现可在 ConfigCard 侧加空态跳过）。
- expand_timing 仍为 opt-in（主线决策：呈现 gate 未过不默认启用）；用户 A/B 后若明确更顺，可推动 Codex 重评默认值。

## Relevant Files

- 源码：`src/ui/fluent_motion.py`、`src/ui/hide_config_rows.py`、`src/ui/responsive_task_config.py`、`src/ui/quest_ui.py`、`src/globals.py`
- 测试：`tests/test_fluent_motion.py`（18 项）、`tests/test_quest_ui.py`（HiddenConfigRowsTest 4 项 + ExpandDurationTest 1 项 + ExpandTimingTest 6 项）、`tests/test_responsive_task_config_ui.py`（quick hunt 断言更新为隐藏期望）
- 离屏帧序列证明：worktree `.local-dev/experiments/ui-fluent-motion-20260824/`（`render_frames.py` + `outputs/page_transition.png`、`outputs/board_entrance.png`）

## Verification

- 分支合并前已过（仓库 venv）：792 项全套、Ruff、compileall、`git diff --check`、src 键盘扫描与 `check_change_budget.py`。
- 合并后主工作区已过（2026-08-24，含仍未提交的其他域测试）：`unittest discover -s tests -q` 808 项 OK、Ruff、compileall、工作区及 `2d7e2af..1c220ec` 空白检查、src 键盘限制扫描。
- 基线对照：`gc: 53 uncollectable objects` 警告在 pristine `2d7e2af` 上同样出现（临时 detached worktree 复核），非本分支引入。
- 实机：日用直接体验（默认开启）；对照关闭态：`OK_BD2_FLUENT_MOTION=0` 回退切页过渡/错落入场/选中条滑动三类动效。**维护行隐藏不受该开关控制**——由 `hide_config_rows.HIDDEN_CONFIG_TOKENS` 决定，改 token 列表重启生效。

## Git State

- 主工作区 `D:\ok-bd2`、分支 `codex/ui-redesign-task-centric`：pending commit `6237fee`；Fluent merge commit `1c220ec`，父提交为 `6237fee` 与 `7dce7d7`。
- 来源分支 `claude/fluent-motion-20260824` 仍指向 `7dce7d7`；worktree `D:\ok-bd2-claude-fluent-20260824` 保留（含未跟踪 `.local-dev` 实验产物），未清理。
- 两笔主工作区提交均仅在本地；未 push / tag / release。map_trade、manual_resolution、main_window_geometry、windows_graphics、config、live_screenshot、日志与 shop PNG 等原有未提交改动均保持在提交范围外。

## Next Steps

1. ~~Codex 按合并 prompt 执行~~ —— 已完成：`6237fee` → merge `1c220ec`。
2. 合并后做一次非阻塞实机 smoke：确认 `7dce7d7` 新增的任务板首次进入错落入场；其余切页残影修复与动效体感已有 2026-08-24 实机确认。
3. 该域可选后续（均非阻塞、用户未要求）：悬停微交互、动效参数微调（如用户对新参数提出体感反馈）。
4. UI 重构域其余遗留（StartTab 三分格重排 / TriggerTaskTab / "执行剩余"批量模式等）仍按 `ui-redesign.md` Remaining 由 Codex 排期。

## Warnings / Constraints

- 本层永不写高度、永不碰展开动画链；`ExpandSettingCard` 高度写入者唯一性约束（CLAUDE.md 关键坑）不受影响。
- 稳态零开销不变量：动效结束后页面/条目不得残留 `QGraphicsOpacityEffect`（回归钉死，改代码时勿破坏）。
- `_StaggerRun` 的中止只挂 Resize：若未来出现"只移动条目不改变任何尺寸"的重排源（当前不存在），错落入场终点会短暂偏旧位置，需补挂对应事件。
