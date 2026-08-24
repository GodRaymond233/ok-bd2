# Codex / GPT-5.6 执行 Prompt — Fluent 动效层合并（2026-08-24）

> 执行状态（2026-08-24）：已完成。pending 五文件提交为 `6237fee`；Fluent merge commit 为 `1c220ec`（父提交 `6237fee` / `7dce7d7`）。仅本地提交，未 push / tag / release；来源分支与 worktree 均保留。

> 用法：将本文件正文直接交给 Codex 侧。权威背景是 `docs/handoff/ui-fluent-motion.md`；源码与 Git 状态若与任何文档冲突，以当前源码/Git 为准，先报告差异再动手。

---

你是 ok-bd2 的 Implementer/Maintainer。任务：把 Claude 已完成、门禁全绿、**用户实机确认**的 Fluent 动效分支合并入 `codex/ui-redesign-task-centric`，并顺带收敛展开动画 pending 的合并。**你自己工作区的未提交域内改动（map_trade / manual_resolution / main_window_geometry / windows_graphics / config / live_screenshot / probe_app1.log / shop PNG 等）全部保留原状，不得混入。**

## 0. 开始前必读（按顺序）

1. `AGENTS.md`（硬约束与门禁定义）
2. `docs/handoff/ui-fluent-motion.md`：Current State → Current Implementation（四轮）→ Rejected → Verification → Git State
3. `docs/handoff/ui-expand-perceived-framerate.md` 的 Current State（只需背景，不必全读）

核验基线：主工作区 HEAD `2d7e2af`；待合并分支 `claude/fluent-motion-20260824`（tip `7dce7d7`，提交链 `8bfa8c1 → 42300e7 → 17a9dd1 → 9d3a6cd → 7dce7d7`，基 `2d7e2af`，未推送）；worktree `D:\ok-bd2-claude-fluent-20260824`。

**评审状态**：`7dce7d7` 为独立评审闭环轮（全新上下文 agent + 作者复审：3 MEDIUM + 5 LOW 全修，无 CRITICAL/HIGH；结论与逐项处置见 `ui-fluent-motion.md`「评审闭环」节）。评审发现并修复了一个设计矛盾：错落入场原在生产不可达（任务板首显必经导航切换被让位跳过），现为 pending 机制——**任务板首次进入以内容错落入场替代整页升起，后续切换照常整页过渡**；此一行为变化用户尚未实机目测（唯一遗留实机项）。

## 1. 分支内容（7 个文件）

| 文件 | 内容 |
|---|---|
| `src/ui/fluent_motion.py`（新） | 切页过渡（新页升起+淡入，**无旧页叠底**——两轮实机反馈钉死）、任务板首次出现的内容错落入场（pending 机制，替代当次整页升起）、首页三栏选中条滑动、`_RefreshDriver` 刷新率驱动（PreciseTimer 按刷新率采样，180Hz→6ms）；`OK_BD2_FLUENT_MOTION=0` / `set_fluent_motion_enabled(False)` 一键回退（中止路径逐项容错并归位）；动效结束摘除全部效果，稳态零开销 |
| `src/ui/hide_config_rows.py`（新） | 隐藏任务卡维护行：token=阈值/秒数/分钟/像素/命中/次数/测试（用户两轮点名）；框架 `config_type['hidden']` 通道 + 从 `sub_configs` 规则剥离（含字符串规则形态；堵住递归创建路径不查 hidden 的框架行为）；配置值与持久化不动，无匹配时完全不触碰任务对象 |
| `src/ui/expand_timing.py`（新） | **与主工作区 pending 最终版逐字节一致**（opt-in 白名单 + 几何签名中止 + trace） |
| `src/ui/responsive_task_config.py` | `responsive_set_expand` 三处修复——**与 pending 逐字一致**（收回终值 `content_height`、BezierSpline (0.4,0)(0.2,1)、时长 `min(420,max(280,240+0.28·ch))` 收回 0.85×、unpolish/polish） |
| `src/ui/quest_cards.py` | `quest_adjust_view_size` 缓存无条件失效——**与 pending 逐字一致**（ExpandTimingTest 内容变化用例依赖它） |
| `src/ui/quest_ui.py` / `src/globals.py` | 接线：quest_ui 末尾 `install_fluent_tab_entrance` + `install_hide_config_rows` + `install_expand_timing`；globals `on_show_main_window` 末尾 `install_fluent_page_transition` + `install_start_list_motion` |
| 测试 ×3 | `tests/test_fluent_motion.py`（18 项）、`tests/test_quest_ui.py`（+HiddenConfigRowsTest 4 +ExpandDurationTest 1 +ExpandTimingTest 6——后六项与 pending 逐字一致）、`tests/test_responsive_task_config_ui.py`（quick hunt 断言更新为隐藏期望，且显式安装 hide 模块保证模块独立可跑） |

分支树上全套 792 项 OK（基线 761 + 31）。

## 2. 与展开动画 pending 的关系（重要）

本分支已含旧 prompt（`ui-expand-merge-codex-prompt.md`）待提交清单第 1–5 项的**全部实质内容**，且与其逐字/逐字节一致。合并本分支后，旧 prompt 只剩两项收尾：

- 第 6 项（CLAUDE.md）：**作废**——CLAUDE.md 在 `.gitignore`（第 3 行），本来就不可提交；
- 第 7 项（docs/handoff 入库）：仍建议执行（见步骤 5）。

另注意旧 prompt 的陷阱条目（expand_timing staged 内容是旧的）已随本分支带入最终版，按第 3 节顺序操作即可规避。

## 3. 执行步骤

1. **核验**：读第 0 节文档；`git status --porcelain`、`git log --oneline -3`、`git -C D:\ok-bd2-claude-fluent-20260824 log --oneline -5`；与文档不符先报告。
2. **先提交你自己的 pending 展开动画文件**（避免带着未提交改动 merge）：`src/ui/responsive_task_config.py`、`src/ui/expand_timing.py`（**先重新 `git add` 覆盖旧暂存**——staged 是缺省 on 时代旧版）、`src/ui/quest_cards.py`、`src/ui/quest_ui.py`、`tests/test_quest_ui.py`。这些与分支内容一致或近乎一致，先落成 commit 让 merge 走"两侧同内容"路径。提交信息沿用旧 prompt 第 4 节建议。
3. **merge 分支**：`git merge claude/fluent-motion-20260824 --no-ff`。预期冲突极少：同内容文件自动收敛；`tests/test_quest_ui.py`（两侧各加了类，位置不同）与 `src/ui/quest_ui.py`（安装行并集）如有冲突按"两边都保留"解决。
4. **门禁**（仓库 venv）：`unittest discover -s tests -q`（合并你自己的新测试后总数 = 787 + 你的新增；全绿为准）+ Ruff + compileall + `git diff --check` + src 键盘限制扫描。
5. **docs 入库**：`git add docs/handoff/*.md` 提交任务记录（`ui-expand-merge-codex-prompt.md` 建议随迁并在文首标注"已被 Fluent 合并 prompt 吸收执行"）。
6. **收尾**：更新 `ui-fluent-motion.md` 与 `ui-expand-perceived-framerate.md` 的 Git State 为实际 SHA；提醒用户合并后 smoke 已由 2026-08-24 实机确认覆盖，无需再跑。
7. **不 push / tag / release**，除非用户在你此次会话中明确授权。worktree `D:\ok-bd2-claude-fluent-20260824` 与分支按惯例保留，不清理。

## 4. 已定决策（不要重新争论）

1. Fluent 动效**默认开启**（用户明确要求的功能；`OK_BD2_FLUENT_MOTION=0` 回退）——与 expand_timing 的 default-off 决策不冲突，那是"替换既有行为的未验证机制"，这是用户点名的新增功能且已实机确认。
2. `OK_BD2_EXPAND_TIMING` 维持 **opt-in**；用户已关闭"下拉帧数"议题（2026-08-24），不据此重评默认值。
3. 切页**不得加回任何形式的旧页叠底/交叉淡出**——两轮实机反馈（不透明垫底、150ms 加速淡出）都被用户读作残影；当前实现（旧页由堆栈原生隐藏）为终态。
4. 隐藏 token 列表为用户两轮点名的并集；恢复某类行 = 改 `hide_config_rows.HIDDEN_CONFIG_TOKENS` 重启，不涉及数据迁移。
5. 隐藏行**不受** `OK_BD2_FLUENT_MOTION` 开关控制（不同关注点）。
6. 高度写入者唯一性 + 公式同步 + 安装顺序（responsive 先、quest_ui 后）不变式继续有效；fluent_motion 不写高度、不碰展开链。

## 5. 风险与行为变化说明

- **默认路径用户可感变化**（均为用户要求且已实机确认）：切页/入场/选中条动效默认开启；任务卡维护行默认隐藏；展开动画时长/缓动/收回终值变化（恒速）。
- `tests/test_responsive_task_config_ui.py` 的 quick hunt 断言已改为隐藏期望并显式安装 hide 模块——单独跑该模块与全套顺序均确定。
- 分支上 `gc: 53 uncollectable` 警告与 pristine `2d7e2af` 相同（已基线对照），非本分支引入。
- `ExpandTimingTest.tearDown` 会把 timing 开关留在 enabled=True 跨模块泄漏——与 pending 行为一致，全套已验证无害。

## 6. 交付物

1. pending 提交 + merge commit 的 SHA 与文件清单。
2. 最终门禁命令与结果（全套全绿为准）。
3. 两个 handoff 的 Git State 更新后状态。
4. 明确标注：本分支内容用户已实机确认（切页残影修复 + 动效体感），无未验证阻塞项；可选项（悬停微交互、参数微调）见 handoff Next Steps。
