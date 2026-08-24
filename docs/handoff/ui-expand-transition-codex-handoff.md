# Codex 交接 Prompt — 展开动画快照回放层（收回残影未解决，移交诊断与后续维护）

> 2026-08-21 Claude → Codex。本文是可直接投喂 Codex 新会话的交接 prompt；细节权威来源为同目录 `ui-expand-transition.md`（Claude 侧 handoff，含完整已否决方案与探针清单）。

---

## 0. 一句话现状

展开回放层（分支 `claude/expand-transition-20260819`，tip `5fddfb8`，804 项测试全过）功能主体完成，quick 白块与启动卡顿已实机确认修复；但用户实机反馈的**"收回时大量残影 + 子任务大框黑色描边增粗"经 Claude 两轮修复（`b53068f`、`5fddfb8`）仍未解决**（用户原话："问题完全没有解决"）。根因未确认——离屏探针复现不了实机症状。Claude 侧到此为止，移交你接手。

## 1. 任务背景

- 目标：任务卡（ExpandSettingCard）展开/收起动画在 60/120/180Hz 下顺滑，不破坏 `setExpand` 语义链，任何失败静默回退原生，kill-switch 默认关闭（不设环境变量 = 原生路径，主线零风险）。
- 该任务属"脚本页面 UI 重构长期例外"授权域（2026-08-18 用户确立），此前由 Claude 独立 branch+worktree 实施。
- 你要做的事：诊断并修复收回残影问题；之后的维护、合并主线、默认启用决策均归你。

## 2. 架构速览（详见 handoff）

- 双载体：**painter**（`OK_BD2_EXPAND_TRANSITION=1`，viewport 子控件 overlay，日用推荐）/ **quick**（`=quick`，QQuickWindow vsync 锁相，实验特性）。均默认关。
- 状态/表现解耦：无动画提交接缝（实例级遮蔽 `expandAni.start` + 显式驱动终态：滚动条终值、`_onExpandValueChanged`、quest chrome 定桩、微动画 settle）；视觉 = before/after 双快照在 overlay 回放（展开 260ms / 收回 220ms，OutCubic）。
- cover-then-commit 两阶段：before 快照先遮盖（painter 用 `repaint()` 同步；quick 等 `frameSwapped`，超时 350ms）→ 遮盖下提交并抓 after → 无缝切入动画。
- 骑边 chrome 条带（`b53068f` 引入、`5fddfb8` 裁剪）：动画边界处重画卡底圆角+描边（qfluentwidgets 的 chrome 是随当前高度自绘的，快照方切会丢圆角——这修的是用户报的"圆角与方形下拉面板同框"）。条带来源=带 body 的快照底部 10 行（展开取 after / 收回取 before），只画在卡体区 `[card_top+min(from_h,to_h), h_t]`、底行锚定动画边界。
- 安全链：安装期+每播 feature-detect、每 tick 签名中止、themeChanged 中止、回放异常会话熔断、kill-switch；回退链 quick→painter→原生；语义 toggle 永不依赖回放成功。
- 仪表：每轮回放一条 `expand replay stats: carrier=... frames=... gap_p50_ms=...` 日志；`expand_transition.dump_diagnostics()`。

## 3. 进度盘点

**实机已确认（用户 2026-08-21 复测）**
- quick 载体白块：已消失 ✓（`53f6931` 移除 transientParent + `e603a0c`/`8b152e7` cover-then-commit）
- 启动卡顿（warm_up 所致）：已消失 ✓（`8b152e7` 删除 warm_up）
- vsync 锁相：120Hz 展开回放 p50≈8.5ms ✓（threaded render loop + D3D11）

**已实现、实机未通过/未确认**
- 展开时"圆倒角与方形下拉面板短时同框"（用户 2026-08-21 报）→ `b53068f` 骑边条带：离屏逐像素对照=原生中段帧；**实机未确认**（后续反馈被收回残影问题淹没）。
- **收回时大量残影 + 子任务大框黑色描边增粗（P0，未解决）**：`b53068f` 后出现/暴露 → `5fddfb8` 条带裁剪到卡体区 → **用户复测：问题完全没有解决**。

**用户明确搁置（勿现在动）**
- 收回方向掉 2× vsync 顿挫（vanish band 每帧错过 deadline）——用户定后续"动效轮"再优化（候选：band 纹理裁剪、异步 image provider、动效参数微调，见 experiments 目录 `entry-flash-and-warmup-plan.md` 阶段 2）。

**未实施**
- painter 入口 34–44ms 优化（悬停预取 before 快照）
- PresentMon 呈现端验证

**已否决方案（勿重试）**：半透明常驻保温窗口（PySide6 6.9.1 DComp 呈现不可靠，≥6.10.2 再评）、show-lowered-then-raise（84–92% 白板）、安装期 warm_up（纯启动卡顿源）、只修提交闪现不修占位色、transientParent（owned window 破坏 flip 上屏）。

## 4. 核心问题：收回残影 + 描边增粗（P0）

**用户描述**：收回动画期间出现大量残影——完整卡片内容的重影（相对主卡下移约 1–2px、半透明感），子任务大框黑色描边明显增粗（单线变 1.5–2 倍）；**快速反复展开/收回**时大量出现。用户测试载体 = painter（`=1`）。触发卡含子任务行（类似"刷压制等级/识别成功后等待秒数/压制OCR阈值"的配置行）。

**诊断现状（Claude 诚实交代）**
- `5fddfb8` 修的 bug（条带末期越界盖住收起卡）真实存在，但实机无效——说明它不是主因。
- 离屏探针（`QT_QPA_PLATFORM=offscreen`，**DPR=1.0**）修复后**两方向**中段帧边界 chrome 与原生逐像素一致 → **离屏模型复现不了实机症状**。
- 量化矛盾：残影是**整卡内容**的重影，而骑边条带只有 10 行，造不出整卡残影 → 真凶大概率不在条带，而在收回方向的 vanish band（`[card_top+to_h, card_top+h_t)` 区间画 before 内容）或 tail band 的合成路径。

**疑点（未验证假设，按优先级）**
1. **DPR≠1（最可疑）**：实机若有 Windows 显示缩放（125%/150%），overlay（viewport 子控件）paintEvent 的 painter 带缩放变换 → compose 里所有 `_blit`（after 底图 / vanish band / 条带 / tail band）变成**缩放采样**，分数逻辑坐标落在分数设备像素上 → 插值/半透明边 → "半透明重影 + 1px 线变 2px 半透明"与症状签名完全吻合。离屏全程 DPR 1.0 所以看不到。旁证：用户实机启动日志有 `SetProcessDpiAwarenessContext() failed: 拒绝访问`（DPI 路径有异常，未定论）。
   **快速验证（5 分钟）**：实机 `QT_SCALE_FACTOR=1` 复测；或直接问用户显示缩放比例。
2. vanish band / tail band 在高 DPI 或大卡（用户批次卡 from_h 可达 ~1600px）下的合成正确性。
3. painter overlay 与主窗 backing store 的合成路径差异（offscreen 无此层）；`WA_OpaquePaintEvent` 假设。

**建议取证顺序**
1. `QT_SCALE_FACTOR=1` 实机复测（区分假设 1）。
2. 实机中段截帧：painter 载体 overlay 是 viewport 子控件，可用 `screen.grabWindow(0, ...)` 全屏抓取后按几何裁剪（quick 的 D3D flip 窗口直接抓不到内容）。现成探针可改造：`D:\ok-bd2\.local-dev\experiments\ui-expand-transition-20260819\probe_real_app_painter.py`（跑完整应用 + 定时 toggle + 抓屏，含 `sys.path.insert(worktree)`）。
3. 日志：每轮 `expand replay stats` + `dump_diagnostics()`；必要时 `OK_BD2_EXPAND_TRANSITION=0` 对照原生确认症状只出现在回放层。

## 5. Git / 仓库状态

- 分支 `claude/expand-transition-20260819`，**未推送**，tip `5fddfb8`，基 `2d7e2af`（`codex/ui-redesign-task-centric` tip）。提交链：`c6a0b6c`→`e26d245`→`c93fe12`→`6819cd4`→`53f6931`→`e603a0c`→`8b152e7`→`b53068f`→`5fddfb8`。
- 独立 worktree `D:\ok-bd2-claude-expand-20260819`（Claude 不再用）。worktree 内未跟踪文件 `qsg_stderr.txt` 是用户实机 QSG 诊断产物，**勿提交勿删**。
- 主工作区（D:\ok-bd2）有你的未提交改动（map_trade / manual_resolution / main_window_geometry / responsive_task_config 等）——合并本分支时勿覆盖。**`responsive_task_config.py` 的 setExpand 优化（unpolish/polish + spaceWidget 高度复用）与回放层入口延迟直接相关，勿回退。**
- 测试：分支上 804 项全过。已知偶发：`test_abort_on_external_height_write_mid_quick_replay` 满载全套下假失败（300ms 断言窗口 < 350ms cover 预算，慢 cover 成功即误报），复跑即绿，可顺手放宽。
- 门禁：仓库 venv `.venv\Scripts\python.exe -m unittest discover -s tests -q` + Ruff + compileall（全局 ok-script 1.0.162 会假失败，必须用仓库 venv）。

## 6. 决策项（归你）

1. 收回残影：继续修（按第 4 节取证顺序），还是暂时搁置——层默认 OFF，主线不受影响；搁置的话建议分支保持未合并。
2. 合并时机与方式；默认启用与否（建议：P0 解决前保持默认 OFF）。
3. 建议把三条 ok-script bump 升级核对（`expandAni` 属性仍在 / `ConfigCard.setExpand` 包装链完整 / `TaskCard.__init__` 包装链完整）增补进 AGENTS.md——任一失效时回放层自动静默回退，仅需人工评估重新适配。

## 7. 复现/验证命令

```powershell
cd D:\ok-bd2-claude-expand-20260819
$env:OK_BD2_EXPAND_TRANSITION = "1"        # painter；=quick 换载体；删掉变量 = 原生
Remove-Item Env:QSG_INFO -ErrorAction SilentlyContinue
Remove-Item Env:QSG_RENDER_TIMING -ErrorAction SilentlyContinue
D:\ok-bd2\.venv\Scripts\python.exe main_debug.py
```

载体变量是**安装时读一次**，换载体需重开应用。快速反复点击卡头展开/收回即可触发 P0 症状。

## 8. 关键文件

- 源码：`src/ui/expand_transition.py`（painter，含 `_draw_edge_strip`）、`src/ui/expand_transition_quick.py`（quick，QML `_QML` 内 `edgeBand`）、`src/ui/quest_ui.py`（install 序列末尾）
- 测试：`tests/test_expand_transition.py`（24 项）、`tests/test_expand_transition_quick.py`（19 项，offscreen 钉 software+basic）
- 探针/报告：`D:\ok-bd2\.local-dev\experiments\ui-expand-transition-20260819\`（probe_real_app*.py、probe_native_vs_replay_chrome.py、probe_collapse_chrome.py、entry-flash-and-warmup-plan.md、probe_app1–15 日志）
- Claude 侧完整 handoff：`docs/handoff/ui-expand-transition.md`

---

*交接人：Claude（2026-08-21）。如需 Claude 侧复查或补充上下文，按分时复用约定另开会话。*
