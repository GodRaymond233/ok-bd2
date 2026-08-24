# Codex / GPT 执行 Prompt — 未提交批次合并前修复(2026-08-24)

> 用法:将本文件正文直接交给 Codex 侧。权威背景是 `docs/handoff/premerge-review-20260824.md`(下称"审查报告");源码与 Git 状态若与任何文档冲突,以当前源码/Git 为准,先报告差异再动手。

---

你是 ok-bd2 的 Implementer/Maintainer。任务:按 Claude 的独立审查结论,修复你自己工作区当前未提交批次(跑商出售 ↑120% 局部 ROI 重做 + WGC resize 稳定补丁 + 主窗口几何防抖 + 手动调整分辨率 UI)中的发现,然后分批提交这批工作。审查基线为 `codex/ui-redesign-task-centric` HEAD `66218a3` 上的未提交快照;若你已继续改动,逐条核对 finding 是否仍成立,失效的标注"已不适用"并说明,不要机械套用。

## 0. 开始前必读(按顺序)

1. `AGENTS.md`(硬约束与门禁定义)
2. `docs/handoff/premerge-review-20260824.md` 全文——15 条 finding(H2/M5)的锚点、证据、修复方向,以及"已验证非问题"清单(**那些不要重查、不要试图"顺手加固"**)
3. `git status --porcelain`、`git diff HEAD --stat` 核对范围

## 1. 修复优先级

### P0(建议提交前必须解决)

- **H1 WGC 最小尺寸门禁**(`src/compat/windows_graphics.py:134`):按第 4 节决策点 D1 处理。最低要求:mid-run 窗口缩小到 720p 以下时不得留下 `connected()=True` 却永无帧的僵尸会话;选择期语义按 D1 结论改。(1280,720) 与 `src/config.py:108` `min_size` 统一为单一来源。
- **H2 手动分辨率原生档位必失败**(`src/ui/manual_resolution.py:180-191`):目标等于面板原生分辨率且确实需要调整时不得直接失败——先按无边框路径(frame delta=0)适配;必须加边框的路径做无变异预校验(如 `AdjustWindowRectForDpi`)再动手,消除"先闪标题栏再报错"。顺带 M1:`get_monitor_area` 改用 `info["Work"]`(工作区),消除 1366×768 类屏幕上任务栏盖住底部客户区导致点击落空的问题。

### P1(同批修,可分开 commit)

- **M2 阻塞卡死**(`manual_resolution.py`):给 `_ResizeJob` 加恢复路径——应用退出/`_shutdown` 钩子取消或止损进行中 job(参照 `LiveScreenshotWidget._shutdown` 模式),挂起时至少让 Apply 按钮可恢复。
- **M5 最大化窗口**:入口处 `IsZoomed` → 先 `SW_RESTORE`(记录 placement,失败路径还原),再走既有流程。
- **M3 出售识别性能**(`trader_sell.py`/`vision.py`):每帧只做一次灰度转换(传入或按 frame 记忆化);同一 `_locate_sale_items` 调用内跨 OCR 高度跳过与已搜 ROI 高重叠(≥90% 面积)的重复搜索;`_status("出售120%局部模板",…)` 改为每轮询一条汇总。
- **M4 售完语义**:`_last_sale_unavailable=True` 的 name-missed 路径(整页无标志 + OCR 见其他文本 + 三高度 miss)补一个测试;582-584 行注释改写为与行为一致的准确表述(该不变量仅在名字被 OCR 看到时成立)。

### P2(清理,顺手做)

- L1:`main_window_geometry.py` 在 closeEvent flush 待决的几何保存(timer active 时 stop + 直调)。
- L2:删 `SALE_ITEM_NAME_LEFT_OFFSET_X` 与 `SALE_120_PERCENT_MARKER_LEGACY_TEMPLATE`(含 trader.py 等 5 处 noqa 再导出行、虚构调用方注释、`tests/test_map_trade_trader.py:464` 钉值断言与 :1215 资产清单项)。`SALE_120_PERCENT_PATTERN` 为先前遗留死代码,可顺手删(注明非本批引入)。
- L3:`main_window_geometry.py` 补丁缩为 moveEvent/resizeEvent 两个包装——删 `stable_event_filter`、`qevent` 参数、`test_main_window_geometry_compat.py:100-103` 对应断言。
- L4:`trader_sell.py:544-557` 验证块删除(None/水平分支无条件不可达;垂直分支仅非 16:9 可达——若要保留对非 16:9 的防御,只留 `vertical_overlap` 一项并注释适用条件);530-534 的中文字符串集合优先级改布尔标志或后写覆盖。
- L5/L6:删除 `probe_app1.log` 与 `recognition-assets/template-assets/shop/sale_120_percent_marker_transparent.png`(提交时绝不能扫入)。
- L8:`stable_close` 改 `with self.lock: _resize_gate(self).clear()`(RLock,一行)。
- 被裁两项:删 `test_manual_resolution_ui.py:143` 恒真 `assertNotIn` 与 `test_map_trade_trader.py:465-466` 常量复述断言;TOCTOU 按 D3 处理。
- L7(do_update 竞争)明确**不修**(上游同模式、0.2s 自愈),不要为它加锁或改线程结构。

## 2. 约束

- 项目硬约束不变:纯鼠标、相对比例坐标、模板+OCR 多重门禁;H1/M3 的修复不得引入键盘、全画面无门禁模板搜索回退。
- 不得破坏审查已确认正确的设计:稳定性门禁 0.8s/帧关闭后重建顺序、identity-only signature、`convert_dx_frame` 仅在 observed==current 时执行的守卫。
- 测试门禁:`.\.venv\Scripts\python.exe -m unittest discover -s tests -v`(仓库 venv,当前 808 项)+ 既有 Ruff/compileall/`git diff --check`/键盘扫描流程。
- 新增测试仅限 M4 的 name-missed 路径与 H1/H2 修复引入的新外部可见行为;修复过程中删除的代码同步删除其钉死测试,不写"防止回归"的守墓测试。

## 3. 执行步骤

1. 核对快照与审查报告差异,失效 finding 标注后跳过。
2. 按 P0 → P1 → P2 修复;每个优先级完成后跑相关模块窄测试,全部完成后跑全套。
3. 分批提交(建议:map_trade 出售识别一批、windows_graphics/WGC 一批、manual_resolution+geometry 一批、清理一批),提交信息用肯定句描述当前状态,不带"移除了被否决的方案"式历史叙事。
4. `docs/handoff/` 入库:本 prompt 与审查报告随批提交;完成后在审查报告文首加一行执行状态(日期、处置概览)。
5. 不 push / tag / release,除非用户此次会话明确授权。

## 4. 决策点(先定再做,必要时问用户)

- **D1(WGC 门禁语义)**:门禁的原始动机是什么(选择期小窗导致 WGC 崩溃?帧垃圾?)。候选:(a) 选择期不做硬门禁,允许 WGC 会话建立,交给稳定性门禁;(b) 保持门禁,但在 `check_resolution` 自动调窗后触发一次捕获重选;(c) 仅保留 mid-run 防线且命中即 `close()`。若动机本身不确定,(c) + 常量统一是保守解。
- **D2(成功后的窗口样式)**:手动调整成功后是否保留 WS_CAPTION(现为测试钉死的既定行为)?若保留,widget tooltip/文案应说明"将转为窗口模式";若改为还原用户原始样式,需同步改 `test_resize_converts_borderless_window_and_verifies_client_size` 的期望。
- **D3(TOCTOU)**:接受为 UX 级守卫(文档化)或在 `SetWindowPos` 前复检一次收窄窗口;不要尝试持 executor 锁(会冻结 GUI start)。

## 5. 交付物

1. 每条 finding 的处置结果(修复/已不适用/决策点结论)。
2. 分批 commit 的 SHA 与文件清单。
3. 最终全套门禁命令与结果。
4. 需实机验证清单的回告计划(H1 前提、M4 频率、M5 触发、H2 闪烁观感、分辨率调整后 WGC 稳定性)。
