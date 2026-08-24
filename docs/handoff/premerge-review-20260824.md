# 合并前独立审查:未提交批次(跑商出售 ROI 重做 + 分辨率/窗口兼容) Handoff

> 审查日期 2026-08-24。审查对象 = 主工作区(`codex/ui-redesign-task-centric`,HEAD `66218a3`)的**全部未提交改动**。审查方式:Claude 独立评审(10 角度 finder → 逐条对抗验证 → 补漏扫描,全程只读)。给 Codex 的执行入口见 `premerge-review-20260824-codex-prompt.md`。
> 若工作区已被改动,以当前源码/Git 为准,先对照本文件核对差异。
> **执行状态(2026-08-25,覆盖下方审查时 Current State):** 15+2 条 finding 已全部处置:14 条修复/清理完成,L7 按审查结论保持不改,TOCTOU 在首次窗口变异前复检任务状态,恒真/复述断言已删除。D1 采用“首次小窗允许 WGC 选择、同一窗口建立合规捕获后缩小即关闭会话”;D2 优先带标题栏工作区方案,边框或工作区无法容纳时使用无边框工作区/整屏方案;D3 不持 executor 锁。等价 Final 门禁为 Ruff、compileall、811 项 unittest、`git diff --check`、src 键盘接口扫描全绿;完整测试退出另有不影响结果的 `ResourceWarning: 53 uncollectable objects`。代码已分批本地提交(map-trade `fd1a488`、Windows/分辨率 `951a8f3`,handoff 由当前文档提交收录),尚未推送;5 项实机验证仍待回告。

## Goal

在把这批未提交改动(跑商出售 ↑120% 局部 ROI 模板重做、WGC 捕获 resize 稳定补丁、主窗口几何防抖、手动调整分辨率 UI)提交/合并前,提供一条独立于实施者的第二判断主线,拦截系统级缺陷。

## Current State

**审查已完成,修复未开始。** 产出 15 条已验证 finding(2 HIGH / 5 MEDIUM / 8 LOW)+ 2 条超上限被裁的 LOW + 2 条被驳回的怀疑。全仓 808 项测试在审查时全绿(测试通过 ≠ 无问题:两条 HIGH 恰好都发生在测试覆盖不到的真实控制流)。修复与提交由 Codex 执行。

## Confirmed Findings(15 条,按严重度)

严重度标准:HIGH=功能在最常见场景下失效或兼容层目的落空;MEDIUM=特定但现实的场景出错/明显性能回退;LOW=自愈型竞争、死代码、遗留物。

### HIGH

**H1. WGC 最小尺寸门禁的双重后果** — `src/compat/windows_graphics.py:134`
小于 1280×720 时 `stable_start_or_stop` 返回 False 的语义与上游契约冲突:
- **选择期降级**:`update.py:50` 把 False 当"WGC 不可用",整轮固定回退 BitBlt_RenderFull;`StartController` 的自动调窗(`check_resolution → try_resize_to`)发生在最后一次捕获重选**之后**,且只有用户手动刷新/重启才重选——Win10 WGC 兼容层的目的在本轮落空(有节流日志,非全静默)。
- **运行中僵尸会话**:窗口缩到 720p 以下时该分支不调 `close()`,`frame_pool` 存活 → `connected()`=True 但 `get_frame` 永远 None;上游 10 秒无帧重启路径在 `if self.start_or_stop():` 之内,不可达。
- 附带:(1280,720) 在 `windows_graphics.py:14` 与 `src/config.py:108`(`supported_resolution.min_size`)两处独立硬编码,可漂移。
修复方向:把"尺寸下限"从 `start_or_stop` 的 False 语义中拿出来(设计决策,见 prompt 第 4 节);至少 mid-run 缩小时走 `close()` 消灭僵尸会话;常量统一来源。
需实机验证:BD2 是否真会在选择时呈现 <720p 客户区。

**H2. 手动分辨率:面板原生档位必失败** — `src/ui/manual_resolution.py:180-191`
WS_CAPTION 强制转换先于适配校验执行:外框 = 目标 + 边框 > 整屏 → 1080p 屏上**默认选项 1920×1080** 只要真的需要调整就必然抛"当前显示器无法容纳"(2560×1440/3840×2160 同理);唯一不失败的情况是客户区已等于目标(早退)。且注定失败的尝试也会先可见地闪一下标题栏(样式先变、校验后炸)。无边框(外框=客户区,frame delta=0)本可精确容纳原生目标。已用 FakeBackend 实证复现调用序列。
修复方向:先按无边框路径适配;必须转换时用无变异预校验(如 `AdjustWindowRectForDpi` 推导 frame)再动手;成功后的样式保留策略需明确(现为测试钉死的"成功即带边框",见 prompt 第 4 节决策点)。闪烁观感需实机确认。

### MEDIUM

**M1. 按整屏而非工作区居中 → 任务栏遮挡** — `manual_resolution.py:73`(`info["Monitor"]`,未用 `info["Work"]`)
1366×768 屏 + 1280×720 目标:外框底 = 744+fH/2 恒 > 工作区底 728,**无论边框多高**约 27–35 行客户区(~4–5%)被置顶任务栏盖住;本项目 `click_relative` 按比例点低行会点到任务栏。进程为 per-monitor DPI 感知(物理像素),无虚拟化缓解;仓库与 ok 包内均无 Work area 用法。修复:改用工作区。

**M2. 挂起窗口阻塞同步 Win32 调用 → Apply 卡死 + 拖住应用退出** — `manual_resolution.py:192`(+260)
`SetWindowLong/SetWindowPos` 同步发消息,游戏挂起时 worker 无限阻塞(3s 超时只限轮询循环);`_busy=True`、按钮禁用后仅 job 完成才复位,无看门狗;线程池为 `QThreadPool.globalInstance()`(与模板网格加载、反馈上传共享),一个 worker 被永久占用。用户点"应用"后立即退出:QThreadPool 销毁等 job,正常路径退出停滞 ~3.5s、挂起路径无限期,且无 `_shutdown` 钩子(参照 LiveScreenshotWidget 模式)。修复:退出钩子 + 至少按钮恢复路径。

**M3. 出售识别性能回退(无标志页)** — `trader_sell.py:490-528`(+`vision.py:203`、`trader_sell.py:462`)
旧代码整页无标志时 1 次全帧模板匹配 + 零 OCR 即返回;新代码模板搜索嵌在 OCR 高度×名框循环内:名字可见但整页无标志时,每 0.5s 轮询 = 3 次全页 OCR(1080p 下三次近乎相同、每次数百 ms)+ ≤9 次 ROI `match_all`(**8/9 冗余做全帧 cvtColor,`to_gray` 无缓存**)+ 3–9 条 `info_set` 日志行;×8s ×当日每条可售条目。修复:每帧一次灰度(按 frame 记忆化或传入)、跨高度跳过高重叠(≥90%)已搜 ROI、`_status` 改每轮询一条汇总。注意:条目不在有标志页时新旧成本相同,回退仅限整页零标志场景。

**M4. 售完判定语义变化 + 未测路径** — `trader_sell.py:585`
移除全帧模板早退后:整页无标志 + OCR 看到其他文本但**持续 ≥8s 三高度** miss 商品名 → `_last_sale_unavailable=True` → 逐条目"已售完"跳过。旧代码同场景是硬失败(中止整个出售批次)——新方向更优雅,但触发是系统性 name-OCR 失败而非瞬时噪声(瞬时被重试吸收)。582-584 行注释的不变量仅在名字被看到时成立;name-missed 路径新旧测试均未覆盖(测试模块无任何 `assertTrue(_last_sale_unavailable)`)。修复:补该路径测试 + 注释写清触发条件;持续 miss 实际频率需实机验证。

**M5. 最大化窗口未处理** — `manual_resolution.py:180`
样式操作不清 WS_MAXIMIZE(全文件零处 IsZoomed/SW_RESTORE/SetWindowPlacement);对 zoomed 窗口 SetWindowPos 只改 normal position(Win32 既定语义)→ 3s 轮询超时报错;失败恢复用缩放态外框 SetWindowPos 同样只覆写 normal position,用户稍后还原窗口时**丢失原窗口ed bounds**。触发需用户手动最大化的带边框窗口(BD2 无边框 WS_POPUP 不带 WS_MAXIMIZE,不受影响);"看似成功"分支不可达。上游 ok 的 resize 工具同样不处理,非相对上游的回归。修复:IsZoomed → 先 SW_RESTORE(记住 placement,失败时还原)。

### LOW

**L1. 退出前 500ms 内的几何变更丢失** — `main_window_geometry.py:26`(补漏轮发现,未单独对抗验证)
上游 eventFilter 在 1.0.190 从未被安装,补丁的 moveEvent/resizeEvent + QTimer 是唯一写者;closeEvent 未被补丁触及,待触发单发 timer 随事件循环死亡 → 退出前最后一次移动/缩放静默丢失,下次启动恢复旧几何。修复:closeEvent flush。

**L2. 死常量 + 虚构调用方注释** — `trader_constants.py:93-112`
`SALE_ITEM_NAME_LEFT_OFFSET_X`:本 diff 删掉了其最后一个真实消费者,现仅剩 5 个模块 noqa 再导出 + 测试钉值 115;`SALE_120_PERCENT_MARKER_LEGACY_TEMPLATE`:仅 trader.py 再导出 + 资产存在性测试,且阈值从改前 0.88 改为 0.80,**不是忠实回滚基线**(git 历史才是回滚路径)。注释声称的 "callers/older modules" 不存在。按工程政策应连常量、注释、再导出链、测试断言一并删除。(`SALE_120_PERCENT_PATTERN` 的死是先前遗留,非本次引入。)

**L3. eventFilter 补丁包裹永不可达方法** — `main_window_geometry.py:41-47`
穷举 ok(唯一命中 touch_scroll.py:98 的独立 guard)、qfluentwidgets(~40 处,全是组件自装)、qframelesswindow(TitleBar 反向装)、本仓库 src/ 的全部 `installEventFilter`:没有任何地方把 MainWindow 装为过滤器。上游 `MainWindow.eventFilter` 本身是死代码;补丁对它的替换不可达,`qevent` 参数仅为它存在,`test_main_window_geometry_compat.py:100-103` 手工调用的是生产无法触发的行为,43-45 行注释描述了不存在的路径。修复:补丁缩为两个事件包装,删 qevent 与死分支测试。

**L4. 不可达几何复验块 + 中文字符串集合控制流** — `trader_sell.py:544-557`(+530-534)
None 分支与水平检查无条件不可达(去重已滤无效框;ROI 右缘就是 `round(name_x)` 且框架 Box 坐标强制 int,matchTemplate 只产生完整落入的位置);垂直检查在 16:9 ≥720p 下不可达(模板高 14px 按 `min(W/1920,H/1080)` 缩放恒大于按 `H/1080` 缩放的 12px padding),仅非 16:9 可达。530-534 优先级集合三条中一条存活、一条无条件死、一条条件死;早前高度的 OCR 级原因不在集合内会被覆盖——优先级不一致,重命名任一字符串静默改控制流。修复:删验证块(要拒贴边就收紧 ROI 右缘 1px);`last_reason` 改布尔标志或后写覆盖。

**L5. `probe_app1.log` 遗留物** — 仓库根
172 字节单行错误日志(`probe_real_app.py` 不存在且从未存在于任何 git 历史);未被 .gitignore 覆盖(`git check-ignore` 退出 1),`git add -A` 会扫入。提交前删除。

**L6. 孤儿模板 PNG** — `recognition-assets/template-assets/shop/sale_120_percent_marker_transparent.png`
全仓库零引用(LEGACY 用 `sale_120_percent_marker.png`,BETA 用 `..._beta_transparent.png`,TradeAssetsTest 清单也不含它),是模板迭代中间产物,与活跃变体仅一词之差易误导。提交前删除。

**L7. worker 调 `do_update_window_size` 与轮询线程竞争** — `manual_resolution.py:165`
与 HwndWindow 自带 0.2s 无锁轮询线程(读后写非原子,中间还插数十至数百 ms 的 handle_mute)交错时可短暂写回并 emit 旧几何,0.2–0.4s 自愈;identity-only signature 使过期几何不会触发 WGC 会话重建,残余仅 crop/缩放 <0.2s。上游 `bring_to_front`/`try_resize_to` 同模式,非新危害类,widget 返回值直接量自 Win32 不受污染。可不修。

**L8. `stable_close` 锁外清 gate** — `windows_graphics.py:205`
全补丁唯一未在 `self.lock` 内的 gate 变更;极端交错下会话重启后一次性多掉 <0.8s 帧(无条件自愈)。修复一行:`with self.lock: _resize_gate(self).clear()`(lock 是 RLock)。

### 超出 15 条上限被裁(已确认,顺手处理)

- **任务启动 TOCTOU**(`manual_resolution.py:106`):GUI 检查与 worker 复查之间 trigger 任务可启动;框架无 API 可彻底封死(`pause()` 是咨询态、持 `executor.lock` 3s 会冻结 GUI start),且框架本就容忍运行中改尺寸。可在 SetWindowPos 前再查一次收窄,或文档化为 UX 级守卫。
- **恒真/复述测试断言**:`test_manual_resolution_ui.py:143` `assertNotIn((800,600),…)` 被上方精确元组 assertEqual 蕴含;`test_map_trade_trader.py:465-466` `assertEqual(150/12, 常量)` 与 ROI 期望断言同败,无独立失败模式。删。

## Rejected / Failed Approaches(已驳回,勿重走)

- **"应复用 ok-script 窗口工具而非自建"** — 驳回。`resize_window` 仅主显示器居中、超时仍无条件返回 True(无法驱动失败恢复);`try_resize_to` 受"Auto Resize"配置门控、按目录自动挑选、不能指定精确目标;`get_window_bounds` 的 DWM 调用在上游是死代码(结果未使用),减法与本地一致且对"按客户区定外框"而言 GetWindowRect−GetClientRect 才是正确口径。自建 ~70 行为合理分叉;仅 ~4 行 caption 舞步与 `show_title_bar` 重叠(复用会失去可注入 backend)。
- **"`_sale_marker_search_roi` 违反 FHD_1080 校准禁令"** — 驳回。`calibration.py` 禁令对运行时缩放代码不成立:6+ 模块(含直接同级 `trader_cartridge.py:221`、`card_status.py:153`)硬编码同一惯用法,新代码符合本模块家族主流写法;新参考像素常量已正确落在 constants 模块。
- 审查中排除的大量假设见下节"已验证非问题"。

## 已验证非问题(防后续重复报)

- identity-only `capture_target_signature` 对 WGC 安全:尺寸变化全部以 ContentSize 形式被稳定性门禁捕获;`real_*`/位置分量不影响 WGC 帧内容;browser.py 的同名 property 是另一类不受影响。
- `last_size` 与帧 ContentSize 不会发散:`convert_dx_frame` 仅在 observed==current 时执行;DXGI 设备错误路径的重置尺寸相同。
- `_ResizeJob` 信号/job 生命周期安全:`_finish()` 在槽内执行时 `self._job` 仍持有 job,排队信号不会被 GC 吞;`_busy` 双击重入安全。
- 补丁幂等标记有效;`(*args)` 回调签名与 TypedEventHandler 注册匹配;`Logger.error(message, exception=)` 存在(info/warning 单参调用合规)。
- WS_POPUP(位 31)样式算术在 Python 任意精度补码下正确;进程 per-monitor DPI 感知,Win32 坐标全物理一致。
- 两张模板 PNG 均 52×14(与常量注释一致);模板缓存按 spec 名隔离无交叉污染;beta 文件名不走 main-region 裁剪,search_roi 分支无不对称。
- 上游无位置参数 `start_or_stop(True)` 调用者(全部无参),`capture_cursor=False` 默认参数安全。
- `install_live_screenshot` 守卫对 manual-resolution card 成立;ok StartTab 无 `manual_resolution_*` 属性冲突;`og.executor.current_task/paused`、`device_manager.hwnd_window/get_preferred_device/do_update_window_size` 均在 1.0.190 存在且语义与守卫假设一致。
- `_deduplicate_ocr_boxes` 保持 confidence 降序,`percent_boxes[0]` 确为最优,margin 检查前提成立。
- `Vision.match_all` 新参为追加默认 kwarg,既有全部调用点(quick_hunt/card_status/navigator 等)不受 `elif` 重排影响。

## Remaining Problems

- 上述 15+2 条待 Codex 修复(入口:`premerge-review-20260824-codex-prompt.md`)。
- 需实机验证清单:H1 前提(BD2 选择时 <720p 客户区是否出现)、M4(无标志页持续 name miss 频率)、M5 触发普遍性(用户手动最大化)、H2 闪烁观感、分辨率调整成功后的 WGC 稳定性重放。

## Relevant Files

改动本体:`src/compat/windows_graphics.py`、`src/compat/main_window_geometry.py`(新)、`src/ui/manual_resolution.py`(新)、`src/ui/live_screenshot.py`、`src/config.py`、`src/tasks/map_trade/{trader_sell,trader_constants,vision,trader}.py`;测试:`tests/test_{map_trade_trader,main_window_geometry_compat,manual_resolution_ui,windows_graphics_compat}.py`。上游参照(`.venv/Lib/site-packages/ok/`):`device/capture_methods/{windows_graphics,hwnd_window,update}.py`、`gui/MainWindow.py`、`script/StartController.py`、`task/TaskExecutor.py`、`util/window.py`。

## Verification

- 全套:`.\.venv\Scripts\python.exe -m unittest discover -s tests -v`(审查时 808 项全绿;必须用仓库 venv,全局 python 的 ok-script 1.0.162 会报假失败)。
- H2 复现:FakeBackend 下 1600×900→1920×1080(1080p 屏)调用序列 = style 转换 → position(原 rect) → 抛"无法容纳"。

## Git State

审查基线:`codex/ui-redesign-task-centric` HEAD `66218a3` + 未提交工作区(8 个修改文件 + 本文件所列 untracked)。审查只读,未做任何修改;`66218a3` 之前已有 fluent merge(`1c220ec`)。审查结论对该快照负责,Codex 修复后如需复审再开新轮。

## Next Steps

1. Codex 按 prompt 修复(优先 H1/H2,含两个设计决策点)。
2. 实机验证清单回告后归档本文件。

## Warnings / Constraints

- 修复 H1 时不得破坏稳定性门禁/identity-signature 的既有设计意图(它们本身已被验证为正确且必要)。
- 修复 M4/L4 时注意 582-584 行注释与行为的对应关系,补测试而非只改注释。
- 本批次提交时**不得**把 `probe_app1.log` 与孤儿 PNG 扫入(L5/L6)。
- 勿重新报告"已验证非问题"节所列各项。
