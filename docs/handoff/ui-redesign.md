# 脚本页面 UI 重构（quest 风 V2）Handoff（ui-redesign）

## Goal

按 mockup V2（`.local-dev/experiments/ui-redesign-20260818/mockup.html`，主工作区本地文件、git 排除）把 ok-script 框架默认任务页重构为 quest 风界面（任务卡/横幅/运行面板/侧栏分组），并保证稳态性能与展开动画质量。**本域是长期例外授权域**：UI 重构需求视为已授权 Claude 直接实施（独立 task branch + worktree + 独立 commit，交回 Codex 维护发布，不覆盖 Codex 工作区），无需逐次确认。Claude Desktop / Claude Code 两侧会话均适用。

## Current State

- **已合并主线**（`codex/ui-redesign-task-centric`）：V2 第一版 `7a18cc5`（分支 `claude/ui-redesign-v2-20260818`，Codex 以 `3fc75e0` 整合，随 0.1.27 开发版运行）；第二轮 `bd766a2`（子开关收回展开区 + 稳态脏检查，fast-forward 合入）；展开动画修复 `fc8f80f`（Codex 以 `2d7e2af` 等价落地，非同一 commit）；PVP 测试基线 ImportError 由 Codex `2e24db7` 修复闭环（PVP 模块 80 项回归恢复）。
- 交付组件：`src/tasks/run_history.py`（北京时间 04:00 日界 / 周一 04:00 周界、子任务扇出计数）、`src/ui/quest_cards.py`（状态圆点/meta 行/徽记配色/合辑子开关在展开区）、`src/ui/quest_banner.py`（今日委托横幅 + 页脚状态栏）、`src/ui/run_panel.py`（运行详情面板）、`src/ui/nav_sections.py`（侧栏组头）。
- 稳态每秒刷新脏检查（内容签名跳过）：离屏基准 4.5ms → 0.13ms/tick。
- 展开动画快照回放层是本域后续独立任务，**未合并**，见 `ui-expand-transition.md`。
- 状态：核心已合并、随开发版日用；5 项遗留决策/实现交回 Codex（见 Remaining）；实机体感未系统回告。

## 2026-08-25 美学打磨轮（aesthetic-v3，用户已批准 mockup）

- 新视觉稿 `.local-dev/experiments/ui-aesthetic-v3-20260825/mockup.html`（白+蓝 Fluent 配色不变，首轮鎏金方案被用户否决后改回）；用户批准后落地到分支 `claude/ui-aesthetic-v3-20260825`（worktree `D:\ok-bd2-claude-aesthetic-v3-20260825`）。
- 改动只涉及样式层（5 文件，无行为变化）：quest_theme 增 `accent_deep` token 与 `rgba()`/`mix()` helper（QSS 不支持多层背景，晕染用不透明预混色烘焙）；横幅/运行面板 14px 圆角 + 淡蓝径向晕染 + 标题 900 字重；环形进度 72px + 锥形渐变；状态印 run 双层光晕+垂直渐变、ok 柔光晕；meta 行双色富文本（前缀按状态着色，心跳比较缓存的 display 串而非 `meta.text()`——富文本下 text() 返回 HTML 源串，直接比较会让每秒心跳误触发）；运行面板强调色描边、分段条执行中段渐变、子任务格 4→3 列 + doing 高亮、键名列宽 76；responsive_task_config 任务卡框架 8px→14px 圆角、标题 15px/700（标题字重/字号变化不影响 50/68 固定头高公式）。
- 独立评审 3 LOW 已闭环：`ring.update()` 进 `_apply_style`（主题翻转重绘，基线既有结构顺手修复）、任务卡圆角补齐、SegmentedBar 段类型注解收紧为 `tuple[int,str] | tuple[int,str,str]`。
- 门禁：816 项全套测试 + ruff + compileall + diff check + 键盘扫描全过；离屏渲染截图（深浅双主题）已目检。
- 同分支第二轮（字体）：全局字体栈改为 MiSans → Microsoft YaHei UI → Microsoft YaHei → Segoe UI（`quest_theme.apply_app_font()`，在 `Globals.on_show_main_window` 调用，缺字体的机器按栈回退）；MONO_FONT 改 JetBrains Mono → Cascadia Mono → Cascadia Code → Consolas → YaHei UI（末尾补 CJK 回退族，修中西文混排）；环形进度数字用 Cascadia Mono。**注意 Qt offscreen 平台拿不到系统字体库，字体渲染验证必须走真实 QPA**（离屏窗口 grab，见 `.local-dev/scripts/render_aesthetic_v3.py --real`）。本机 MiSans 仅 Regular 字重，900 标题为 Qt 合成加粗，观感已目检可接受。
- 同分支第三轮（状态印修复，用户实机反馈）：**QColor 的 rgba() 字符串解析拒绝 0-1 浮点 alpha，静默变成不透明黑**——painter 侧着色必须 `QColor(hex)` + `setAlphaF()`，QSS 字符串才能用 `quest_theme.rgba()`；回归测试 `SealDotTest.test_run_seal_halo_is_accent_tint_never_black` 钉住。圆点 9→11px；run 态加呼吸效果（`_SealBreathDriver`：正弦相位 1600ms 周期、`_refresh_interval_ms` 刷新率驱动，无 run 态 seal 时定时器停止、稳态零开销）。
- 待办：Codex 合并决策；实机目检 900 字重表现、渐变晕染与状态印呼吸观感。

## Confirmed Findings

- `4b85dab` 时期 `default_config` 缺"启用"键导致三连锁：开关不渲染、用户保存值被丢弃、sub_configs 无挂载点（`547cc7a` 修复，审查确认定位准确）。
- 稳态卡顿根因 = 每秒定时器路径上的无条件控件操作：`RunPanel.render` 每秒销毁重建全部行控件（done 面板因 `keep_info_when_done=True` 永久可见，重建不停）；任务卡心跳每秒 `setText` + `heightForWidth` 全量遍历。
- 展开动画原根因（高度双写入者每帧覆盖动画值）与修法详见 `ui-expand-transition.md` Confirmed Findings。
- 批量任务 info 的 完成/失败/跳过 是"、"连接串，而子任务名自身含"、"（公会、小屋、酒馆）——解析必须用 `run_history.contains_joined_name` 边界匹配。

## Current Implementation

- 高度写入者仅两处且公式必须同步：`quest_cards.py` 的 `apply_quest_chrome`（头部 50/68 + 视口边距 + 总高）与 responsive 补丁 `_adjustViewSize`（spaceWidget + 展开总高）；回归 `test_batch_card_expand_and_collapse_reveal_child_switches`（收起精确回到头部高度）。**动画运行期间 `expandAni` 是唯一高度写入者**（回归 `test_expand_and_collapse_animate_progressively`）。
- 合辑子开关在 `viewLayout` 展开区：启用总开关 + 6 个框架 sub_configs 缩进开关 + 重置配置行；主开关关闭时子开关按框架规则隐藏。
- 网格单元格样式随主题签名重建；新组件自身令牌覆盖深色，任务卡框架区依赖 qfluentwidgets 全局刷新。

## Rejected / Failed Approaches

- **合辑子开关头部常驻面板**（V2 第一版 `_install_sub_panel`/`quest_sub_height`/`_style_sub_panel`）：对 mockup 的误读；用户明确子开关应与独立任务卡一致——点击展开后才显示。`bd766a2` 整体删除，勿复刻。
- **ExpandSettingCard 高度多写入者**：两个补丁各写一版高度会振荡至死循环（V2 时期实测；回归 `test_batch_card_expand_and_collapse_keep_sub_panel_geometry` 时代钉过）。

## Remaining Problems（前五项交回 Codex 决策/实施）

1. 横幅"执行剩余"按钮未实现：需 `DailyBatchTask` 支持"仅执行今日未完成"运行模式（任务逻辑属 Codex 域，UI 未越界）。建议为批量任务加显式参数，勿临时改持久化配置。
2. 截图方式页（StartTab，mockup 窗口三）未重排（选择窗口/截图方式/交互方式三分格 + 开发工具区仍为框架现状）。
3. TriggerTaskTab（实时触发页）仍用框架原始"信息/值"表；如需统一可复用 `src/ui/run_panel.py`。
4. "每周跑图"本周完成判定以运行记录近似（本周成功跑过一次即算完成），未接 `ProgressStore` schema 4 周进度；精确口径由 Codex 接真实进度源。
5. 深色切换的任务卡框架区全局刷新路径实机确认。
- 实机体感清单（用户回告）：真实字体排版间距、深浅色切换、任务卡点击展开动画（含合辑卡）、运行面板与横幅在真实任务运行中的体感、横幅按钮启动批量、稳态每秒卡顿是否消失。

## Relevant Files

- 源码：`src/ui/quest_cards.py`、`src/ui/quest_banner.py`、`src/ui/run_panel.py`、`src/ui/nav_sections.py`、`src/tasks/run_history.py`、`src/ui/responsive_task_config.py`（Codex 域）
- 测试：`tests/test_quest_ui.py`、`tests/test_run_history.py`
- 设计稿：`.local-dev/experiments/ui-redesign-20260818/mockup.html`；性能基准：`.local-dev/experiments/ui-perf-20260818/bench.py`

## Verification

- 门禁（仓库 venv）：`.\.venv\Scripts\python.exe -m unittest discover -s tests -v` + Ruff + compileall + `git diff --check` + src 键盘扫描（禁键盘硬约束）。
- 本任务标准验证手段：离屏渲染（含合辑展开态截图）+ 真实任务类整机 MainWindow 构建冒烟（子开关在展开区、收起 50px/展开 462px）。

## Git State

- Claude 分支：`claude/ui-redesign-v2-20260818`（`7a18cc5`，已整合）、`claude/ui-batch-expand-perf-20260818`（`bd766a2` 已 ff 合入；`fc8f80f` 以 `2d7e2af` 等价落地）。对应 worktree `D:\ok-bd2-claude-ui-v2-20260818`、`D:\ok-bd2-claude-ui-fix-20260818` 仍在（按 AGENTS.md 约定不清理附加 worktree）。
- Claude 侧本域无未提交工作。

## Next Steps

1. 用户实机体感清单回告。
2. Remaining 1–5 由 Codex 决策排期（"执行剩余"依赖批量任务模式改造，优先级最高的一条链）。
3. 展开动画回放层的后续见 `ui-expand-transition.md`。

## Warnings / Constraints

- 高度写入者两处公式同步（见 Current Implementation），改任一必须同步另一。
- "、"连接串边界匹配坑（见 Confirmed Findings），禁止 `split("、")`。
- **主工作区 `responsive_task_config.py` 有未提交改动**：setExpand 70ms 阻塞的优化（`style().unpolish(self)/polish(self)/update()` 替代 `setStyle(QApplication.style())`，并复用 `_adjustViewSize` 已写入的 `spaceWidget.height()` 避免二次内容遍历）——**勿回退**，勿未经验证恢复旧写法。
- 本域一切改动走独立 branch + worktree、独立 commit、不覆盖 Codex 工作区（含其未提交修改），完成后交回 Codex。
