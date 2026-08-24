# Codex / GPT-5.6 执行 Prompt — 展开动画改动合并（2026-08-24）

> 状态（2026-08-24）：产品链第 1–5 项已被 Fluent 合并流程吸收，提交为 `6237fee`，并由 merge commit `1c220ec` 合入 `codex/ui-redesign-task-centric`。`CLAUDE.md` 因被忽略未提交；本 prompt 的 alpha-fix 对照分支步骤不属于此次 Fluent 合并，仍保持独立。

> 用法：将本文件正文直接交给 Codex 侧 GPT-5.6。权威背景是 `docs/handoff/ui-expand-perceived-framerate.md`（尤其「审查闭环（2026-08-24）」「Current Implementation」「Git State」三节）；源码与 Git 状态若与任何文档冲突，以当前源码/Git 为准，先报告差异再动手。

---

你是 ok-bd2 的 Implementer/Maintainer。任务：把 Claude 已完成并通过门禁的「展开动画生成端修复 + 审查闭环」改动，从主工作区脏状态中**逐文件选择性**提交入 `codex/ui-redesign-task-centric`，并在 alpha-fix worktree 分支上单独提交该处安全修复。**你自己的未提交改动（map_trade / manual_resolution / main_window_geometry / windows_graphics / config / live_screenshot 及其测试、probe_app1.log、shop PNG 等）全部保留原状，不得混入本次提交。**

## 0. 开始前必读（按顺序）

1. `AGENTS.md`（硬约束与门禁定义）
2. `CLAUDE.md`（协作章程；已更新「关键坑」一节，见下文清单第 6 项）
3. `docs/handoff/ui-expand-perceived-framerate.md`：Current State → 审查闭环（2026-08-24）→ Current Implementation → Git State → Next Steps
4. 姊妹文档 `ui-expand-transition.md`（只需读 P0/F31 背景即可，不必全读）

然后核验：`git status --short`、`git log --oneline -3`、下述每个文件的实际 diff。预期基线：HEAD `2d7e2af`，783 项测试全过（2026-08-24 已验证）。

## 1. 待提交清单（主工作区，7 项）

| # | 文件 | 状态 | 内容 |
|---|---|---|---|
| 1 | `src/ui/responsive_task_config.py` | M | `responsive_set_expand` 三处修复：收回终值 `content_height`（F1 死区：原 `verticalScrollBar().maximum()` 使后半程全程无位移）；BezierSpline `(0.4,0)(0.2,1)` 缓动（F2：OutExpo 首帧瞬移 84px 的反面）；时长 `min(420, max(280, 240+0.28·content_height))`、收回 0.85×。**注意**：同文件内 `setStyle(QApplication.style())` → `unpolish/polish` 那一块**不是本任务改动**（你的 a343 worktree 有同款），是否随本 commit 携带由你判断。 |
| 2 | `src/ui/expand_timing.py` | **AM（陷阱见下）** | 新模块：QTimer 刷新节拍驱动原生 value 链。opt-in 白名单（`OK_BD2_EXPAND_TIMING ∈ {1,true,on,yes}`，缺省与拼写错误=off）；`ani.state` 实例阴影维持 sole-writer 门禁；kill-switch 落回未改写原生链；trace 诊断（`OK_BD2_EXPAND_TIMING_TRACE=1`）；**几何签名门禁**（`(view.width, spaceWidget.height)` O(1) 探针，变化即停驱并按当下几何落终态，`_abort_on_geometry_change`）。 |
| 3 | `src/ui/quest_cards.py` | M | `quest_adjust_view_size`：`_quest_content_cache` 失效从"动画期不失效"改为**无条件失效**（漏斗调用事件驱动，至多一帧重走一次布局；不失效则几何探针拿陈旧值）。总高写入门禁不变。 |
| 4 | `src/ui/quest_ui.py` | M (+2) | `install_quest_ui()` 末尾追加 `install_expand_timing()`（幂等、default-off，缺省对用户零影响）。 |
| 5 | `tests/test_quest_ui.py` | M | `ExpandTimingTest` 共 6 项：既有 3 项（sole-writer 门禁与阴影清理、中途反向落对侧终值、kill-switch 原生回退）+ 新增 3 项（中途 resize 双方向落重算终态、隐藏配置行走漏斗触发几何中止、动画期同几何重测不写总高）。 |
| 6 | `CLAUDE.md` | M | 「关键坑」修正：运行时高度写入者为 `apply_quest_chrome` ↔ `quest_adjust_view_size`（quest_cards 后装整体替换 responsive 同名方法，responsive 版是死代码；`src/config.py` 安装顺序 responsive 先、quest_ui 后，不可翻转）；补 timing 几何中止一句。 |
| 7 | `docs/handoff/*.md` | 未跟踪 | 本任务全部 handoff（含本 prompt）。建议随 docs 一并入库以固化任务记录；`_TEMPLATE.md` 是否入库由你定。 |

### ⚠️ 陷阱：`expand_timing.py` 的 staged 内容是旧的

该文件处于 `AM`：**staged 的是早期版本（缺省 on 时代，无几何门禁）**，工作区版本才是最终版（opt-in + 几何门禁）。提交前必须重新 stage 最终内容（`git add src/ui/expand_timing.py`），不要直接 commit 现有暂存区。**禁止 `git add .` / `git add -A` / 整目录暂存。**

## 2. alpha-fix worktree 单独提交（不进产品链）

Worktree `D:\ok-bd2\.local-dev\worktrees\expand-transition-alpha-fix`（分支 `codex/expand-transition-alpha-fix-20260821`，HEAD `5fddfb8`），Claude 于 2026-08-24 修改了 4 个文件，属审查 CRITICAL 闭环，**在该分支上单独 commit**：

1. `src/ui/expand_transition.py` — `_env_carrier()` 缺省 `painter` → `off`（opt-in；DWM 交接 P0 未解，缺省不得启用）
2. `src/ui/quest_ui.py` — 安装注释改为 opt-in 口径
3. `tests/test_expand_transition.py` — `test_env_switch_parsing` 未设环境变量断言 → `assertFalse`
4. `tests/test_expand_transition_quick.py` — `test_env_carrier_parsing` 与 `test_env_config_application_sets_carrier_and_enabled` 的缺省断言 → `off` / `False`

该 worktree 其余未提交改动（DailyTask / QuickHuntTask / map_trade/vision / live_screenshot / monitor 脚本等）是你的域，勿混入此 commit。分支本身维持"对照实验"定位，不合入产品链。

## 3. 已定决策（2026-08-23 审查 + 2026-08-24 用户决定，不要重新争论）

1. native timing **default-off**（opt-in 白名单语义：拼写错误不得启用）——呈现 gate 未通过前不重评默认值。
2. 不迁移载体、不加第四套机制、timing/transition 两个 installer 不得叠装（已核实当前无叠装）。
3. painter 载体 P0 未解不生产化；quick 维持实验对照；`expand-transition-alpha-fix` 分支保留不删。
4. `expand-native-vsync` worktree（305 行版）**冻结**：不合并、不 cherry-pick、不删；其未提交测试改动已逐项审阅，全部为 360ms 调参的等待膨胀，无吸收价值。
5. 420ms 时长上限维持；`content_height` 收回终值在 `ScrollBarAlwaysOff` 契约下正确。
6. **用户已决定跳过全部呈现端取证（2026-08-24）**：R1/R2/R5 归因保持"未知"；补充观察已记录（卡顿与任务运行无关）。合并不以此为先决。
7. 高度写入者唯一性 + 公式同步（`apply_quest_chrome` ↔ `quest_adjust_view_size` ↔ `quest_expand_value_changed`）+ 安装顺序不变式，是本次审查的重点保护对象。

## 4. 执行步骤

1. **核验**：按第 0 节读文档、查状态、比对清单；与文档不符处先报告。
2. **基线复跑**（仓库 venv，全局 ok-script 1.0.162 会假失败）：
   - 聚焦：`.venv/Scripts/python.exe -m unittest tests.test_quest_ui tests.test_responsive_task_config_ui -v`（预期 45 项 OK）
   - 全套：`.venv/Scripts/python.exe -m unittest discover -s tests`（预期 **783 项 OK**）
3. **审查 diff**（要点清单）：
   - `expand_timing.py`：opt-in 白名单判据；installer 幂等；kill-switch 直落原生链；几何中止后 `_DRIVERS` 弹出与 `state`/`start` 阴影清理；异常静默回退不破坏 toggle 语义。
   - `quest_cards.py`：无条件失效后**逐帧仍命中缓存**（只有漏斗调用触发一次重走）；总高写入门禁保留。
   - `responsive_task_config.py`：终值/缓动/时长与 `quest_expand_value_changed` 的高度公式一致性（`max(header + content_height - bar, header)`）。
   - 测试断言保护的是行为语义而非 305 版调参（无 360/320ms、无 InOutSine、无半刷新率字样）。
4. **提交主工作区**（建议拆两个 commit，具体由你定，message 用肯定式语言）：
   - ① `fix(ui): 展开动画生成端修复与刷新节拍重定时候选（默认关闭）` —— 第 1–5 项（若 unpolish/polish 块不携带则第 1 项需部分暂存）。
   - ② `docs(ui): 展开动画任务 handoff 与关键坑修正` —— 第 6–7 项。
5. **提交 alpha-fix worktree**：`fix(ui): 展开回放载体改为显式启用`（仅上述 4 文件）。
6. **最终门禁**：按 `AGENTS.md` Final 模式（Ruff、compileall、仓库 venv 全套 unittest、`git diff --check`、src 键盘限制扫描）；alpha-fix 分支跑 `D:/ok-bd2/.venv/Scripts/python.exe -m unittest tests.test_expand_transition tests.test_expand_transition_quick`（预期 60 项 OK，注意该 worktree 无自己的 venv，用主仓 venv）。
7. **收尾**：更新 `ui-expand-perceived-framerate.md` 的 Git State 节为实际提交 SHA；提醒用户做一次合并后实机 smoke（非阻塞、非验收：展开/收回体感、中途 resize、任务运行中展开）。
8. **不 push / tag / release**，除非用户在你此次会话中明确授权。

## 5. 风险与行为变化说明（供你审查时对照）

- **默认路径用户可感变化来自 F1/F2**（有意修复）：原生动画的缓动、时长、收回终值都变了；timing 模块 default-off 不改变默认路径。
- `quest_adjust_view_size` 无条件失效的代价：每次漏斗调用后至多一个动画帧多走一次 heightForWidth；逐帧成本不变。
- 安装顺序（responsive 先、quest_ui 后）一旦翻转，`_adjustViewSize`/`_onExpandValueChanged` 会退回无门禁的 responsive 版本——CLAUDE.md 已记录，不要"顺手统一"两份实现（统一是另一个任务，不在本次范围）。
- `tests/test_quest_ui.py` 的 `ExpandTimingTest.tearDown` 会把全局 timing 开关留在 `enabled=True` 跨模块泄漏——既有行为，全套测试已验证无害，不在本次修改范围。

## 6. 交付物

1. 两个（或按你拆分）主工作区 commit SHA + alpha-fix 分支 commit SHA，及每个 commit 的文件清单。
2. 最终门禁命令与结果（全套 783 项为准）。
3. 未提交残留清单（应仅剩你自己的域内改动）。
4. handoff Git State 更新后的状态。
5. 明确标注"未验证"项：呈现端 gate（用户决定跳过）、实机 smoke（待用户）。
