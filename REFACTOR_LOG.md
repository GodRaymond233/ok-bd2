# ok-bd2 重构记录（阶段一至阶段六）

本文档按用户要求记录 2026-08-10 至 2026-08-11 六次重构改动的**大纲、主要内容与思路**，用于后续管理与维护。
每个阶段包含：目标与范围（大纲）、具体改动（主要内容）、设计动机（思路）、验证与审阅结论、
提交与备份状态。代码以本地 `main` 分支为准。

---

## 〇、六次改动总览

| 阶段 | 主题 | 核心目标 | 提交 | 状态 |
| --- | --- | --- | --- | --- |
| 阶段一 | 摘除测试兼容层 | 消除正式路径与测试兼容层的双轨并存，让正式账本路径成为唯一路径 | `6dc8b58` | 已提交、已推送、已备份 |
| 阶段二 | 统一视觉识别管线 | 消除五份重复模板匹配实现，修复 `map_trade/vision.py` 对任务层的反向依赖 | `0656264` | 已提交、已推送、已备份 |
| 阶段三 | 拆分大模块与状态机 | 拆分 navigator/trader/collector 千行模块，重构 `_use_action` 状态机 | 本文档下方记录 | 已提交本地、未推送 |
| 阶段四 | 统一输入与坐标模型 | 收敛 drag/scroll 到基类、统一参考分辨率与技能槽坐标、PVP 特殊页下沉 | `aaf6a0a` | 已提交本地、未推送 |
| 阶段五 | 错误语义与性能修复 | mark_target 失败语义、跑商盲点移除、classify 局部 ROI、QuickHunt 组合化 | 本文档下方记录 | 已提交本地、未推送 |
| 阶段六 | 仓库与测试卫生 | 拆分 map-trade 测试、隔离调试任务、统一版本来源、修复 Meta-Review 回归、盘点本地清理项 | 本次提交并推送 | 已实现、独立审阅 PASS |

六次改动的共同约束（来自 AGENTS.md）：

- 禁止在 `src` 中引入项目自定义键盘发送/按下/释放/热键注册/键盘映射。
- 每次改动后必须完整执行单元测试（当前 580 项）、Ruff、compileall、`git diff --check` 与键盘限制扫描。
- 保留用户已有未提交修改，不覆盖或回退无关文件。
- 大规模重构必须保持行为等价，以测试为保护网，并在完成后由独立只读子代理审阅。

---

## 一、阶段一：摘除测试兼容层

### 1.1 大纲

- 目标：删除 `Collector` / `ProgressStore` / `Navigator` / `Trader` 中长期保留的
  `durable_actions` 与 `formal` 双轨、旧参数回退、测试替身接缝，让正式路径成为唯一路径。
- 范围：`src/tasks/map_trade/` 下四个核心模块、`BD2MapCollectionProbeTask` 与对应测试。
- 提交：`6dc8b58 refactor(map-trade): 摘除测试兼容层并统一正式接口`（7 文件，+330/−339）。

### 1.2 主要内容

- `Collector`：
  - `_use_action` / `_use_actions` / `_start_search` 改为必填 `card_id` + `map_role`，
    正式账本路径成为唯一路径。
  - 删除 `_read_count_window` 旧两参回退 helper 与 `_use_skills` 兼容 helper。
  - 技能组 1 恢复不再区分卡带上下文（所有调用均显式带角色）。
- `ProgressStore`：
  - 删除 `require_actions` 参数及 `action_record` / `reserve_action` / `mark_clicked` /
    `mark_local_done` / `settle_pending_actions` 别名。
  - `mark_target` 一律要求 durable 动作记录。
- `Navigator`：
  - 删除 `MAP_LEFT/RIGHT_TEMPLATE`、`AREA_MAP_TITLE_OCR_*_ROI` 兼容常量、
    `locate_story_card_for_probe`、`_area_map_teleports`、
    `open_teleport_map_from_sandbox(map_open_point)` 参数与 `del map_open_point`。
  - 删除 `vision.template_hsv_color_ratios` 的 `getattr` 测试替身回退。
  - `BD2MapCollectionProbeTask` 改用 `locate_probe_story_card` 返回值语义（None = 未找到）。
- `Trader`：
  - 删除 `_confirm_sale` / `_locate_sale_item` / `_wait_sale_item_point` 兼容包装，
    统一走 `_locate_sale_items` / `_wait_sale_item_candidates` 多候选接口。
- 测试：
  - 同步迁移到公共/正式接口；`mark_target` 调用前用 `_seed_action_records` /
    `_seed_battle_supplements` 持久化 durable 记录；`_read_count_window` 替身统一 `**kwargs`。

### 1.3 思路

- 兼容层是历史演进中为降低测试改造成本而保留的“第二套路径”，代价是每次行为修改都要
  同时维护两套语义，容易造成正式路径与测试路径分叉，也让后续重构（阶段二/三）无从下手。
- 先让全部测试迁移到正式接口并跑绿（554 项），确认正式路径已被测试充分覆盖后，
  再一次性删除兼容层；删除顺序为 Collector → ProgressStore → Navigator → Trader，
  最后清理测试中的替身接缝，避免中途出现“测试依赖已删代码”的窗口期。
- 技能组 1 恢复语义由 `Collector.run` 的调用上下文保证（所有调用都带角色），
  因此删除卡带上下文分支不会改变实机行为。

### 1.4 验证与审阅

- 554 项完整单元测试、Ruff、compileall、`git diff --check` 与 src 键盘限制扫描全部通过。
- 独立只读子代理按六项验收标准审阅后裁决“可合入，无阻塞项”；确认兼容层无残留、
  调用点全部显式传 `card_id` + `map_role`、测试迁移未弱化断言。
- 非阻塞观察：计划文档曾把 `_use_skills` 误记为 Navigator（实际在 Collector）；
  技能组 1 恢复的安全性由 `Collector.run` 调用上下文保证。

### 1.5 提交与备份

- `6dc8b58` 已提交并随 `18294f6` 推送至 `origin/main`。

---

## 二、阶段二：统一视觉识别管线

### 2.1 大纲

- 目标：消除 DailyTask / FreeGachaTask / PVPTask / SquareGoddessTask / AutoLoginTask
  五份各自重复的模板匹配实现，统一为共享引擎，并修复 `map_trade/vision.py` 对任务层的反向依赖。
- 范围：`src/utils/` 新增共享规格与引擎，五个任务改为薄封装，`map_trade/vision.py` 委托共享引擎。
- 提交：`0656264 refactor(vision): 统一视觉识别管线并去除五份重复实现`
  （16 文件，+666/−1134，净删约 773 行）。

### 2.2 主要内容

- 新增 `src/utils/vision_models.py`：TemplateSpec / MatchResult / EMPTY_MATCH。
- 新增 `src/utils/task_vision.py`：load_template / match_template / passes_match /
  brightness_ratio / resolve_match_threshold。
- `green_mask_from_template` 从 `BaseBD2Task` 下沉到 `src/utils/image_utils.py`；
  `BaseBD2Task` 反向再导入，依赖方向单向正确（utils 不导入 src.tasks）。
- `map_trade/vision.py`：`Vision.match / passes / _load` 委托共享引擎，
  保留 1280×720 ROI 参考、`threshold_for` 钳制与测试 loader 接缝。
- 五个任务删除各自 TemplateSpec / MatchResult 定义与 `_match` / `_load_template` /
  `_read_template_and_mask` / `_passes` / `_home_brightness_ratio_for_template` 重复实现，
  统一改为共享规格 + 共享引擎委托；各任务保留薄封装（缺失模板日志、内存错误暂停、亮度中心差异）。
- 白盒测试同步迁移到共享类型与共享引擎补丁点
  （image_utils / template_resolution / task_vision）。

### 2.3 思路

- 五份实现来自不同任务、不同时期，表面相似但细节各异（min_size、搜索区、亮度中心、
  绿幕容差等），直接“复制一份再删四处”会丢失真实差异。
- 因此先抽象共享规格与引擎，把每个任务的真实差异显式保留为参数或薄封装，
  再逐任务迁移并跑全量测试；迁移顺序从依赖最少的 map_trade 开始，
  最后处理差异最大的 DailyTask / AutoLoginTask。
- 依赖方向修复独立进行：先让 `Vision` 不再依赖任务层，再删除任务层内重复实现，
  避免中途出现循环导入。
- 非阻塞观察（当前无实际影响，未来维护需注意）：
  1. 绿幕容差统一为严格 0（Daily/AutoLogin 旧实现为 ±4）；实测全部绿幕资产两种容差
     掩码差异为 0，新增含近绿像素模板时需留意。
  2. 非绿色 RGBA 模板现在会生成 alpha 遮罩（旧 Daily/AutoLogin 忽略 alpha）；
     实测当前资产 alpha 全为 255，最终归并为无遮罩，无差异。
  3. 共享引擎缓存键为 `spec.name`（旧 map_trade 用 `file_name`），当前无行为影响。
  4. 共享 `reference_roi_frame` 无宽高 ≥1 下限钳制，仅在 ROI 缩放到 0 的退化帧下
     才可能与旧 `Vision.reference_roi` 不同。

### 2.4 验证与审阅

- 554 项完整单元测试、Ruff、compileall、`git diff --check` 与 src 键盘限制扫描全部通过。
- 独立子代理审阅裁决 PASS（可合入，无阻塞项），主线程按同一清单独立复核一致。
- 本次对话子代理消息机制多次异常（review_stage2_fast 空转、v3 长时间无响应后自行完成），
  后续派发审阅子代理应使用 `fork_turns=all` 且初始消息携带完整任务。

### 2.5 提交与备份

- `6dc8b58`、`0656264` 与文档提交 `18294f6` 已推送至 `origin/main`。
- 备份位于 `D:\ok-bd2-backups\ok-bd2-main-18294f6-20260810-211139\`。

---

## 三、阶段三：拆分 navigator / trader / collector 大模块与 `_use_action` 状态机

### 3.1 大纲

- 目标：把三个千行级模块拆成“门面 + 职责单一的子模块”，并把 `Collector._use_action`
  335 行 / 圈复杂度 73 的状态机拆成可独立维护的分阶段方法；全程不改实机行为。
- 范围：navigator / trader / collector 三个模块及其测试补丁点。
- 提交：本文档下方记录（本地提交，未推送）。

### 3.2 主要内容

- `navigator.py`：3383 行 → 412 行门面，保留 `Navigator` 类与全部导出符号；
  新增：
  - `navigator_constants.py`（488 行）：模块级常量、dataclass、`_sandbox_skill_template`。
  - `navigator_story.py`（1033 行）：`StoryCardNavigationMixin`（剧情卡带导航）。
  - `navigator_sandbox.py`（1660 行）：`SandboxNavigationMixin`（箱庭/传送阵导航）。
  - `navigator_trade.py`（445 行）：`TradeNavigationMixin`（跑商导航）。
- `trader.py`：1805 行 → 205 行门面；新增：
  - `trader_constants.py`（172 行）：常量与 5 个 dataclass、`split_items`。
  - `trader_cartridge.py`（599 行）：`ShopCartridgeNavigationMixin`。
  - `trader_buy.py`（276 行）：`BuyFlowMixin`。
  - `trader_sell.py`（805 行）：`SellFlowMixin`。
  - `trader_pricing.py`（206 行）：`PriceDiscoveryMixin`。
  - 顺带修复 `trader_sell.py` 中 `_selected_quantity_from_text` 对
    `Trader._quantity_from_text` 的类名自引用（改为 `SellFlowMixin`）。
- `collector.py`：1381 行 → 430 行门面；新增：
  - `collector_constants.py`（151 行）：技能识别/OCR/反馈常量、4 个 dataclass、
    相对比例换算 helper。
  - `collector_skills.py`（988 行）：`SkillExecutionMixin`（技能检测、倒计时、
    次数窗口、动作执行与对账）。
- `_use_action` 状态机重构：
  - 原 335 行 / 圈复杂度 73 → 编排入口 76 行 / 圈复杂度 7 + 五个阶段方法。
  - `_existing_record_outcome`：已有 local_done / pending / settled / preexisting_used
    记录的早退判定。
  - `_resume_pending_intent`：armed / clicked / blocked 的重启恢复（USED 则完成，
    否则禁止重复点击）。
  - `_resolve_preexisting_used`：USED 状态下的待对账结算、covered_observed 归因、
    `mark_action_preexisting_used` 额度保留。
  - `_prepare_before_click`：计数窗口读取、既有 pending 结算、额度/已达上限检查、
    `arm_action`；成功返回 `(before, None)`，失败返回 `(None, 结果)`。
  - `_finish_after_click`：点击后反馈/图标/次数组合判定（明确失败、成功结算、
    证据不一致），沿用 post OCR 软门槛与延迟对账语义。
- 测试补丁点迁移（tests/test_map_trade.py，共 8 处）：
  - `navigator.monotonic` → `navigator_sandbox.monotonic`（5 处）/
    `navigator_trade.monotonic`（2 处）。
  - `collector.monotonic` → `collector_skills.monotonic`（1 处）。
  - `COLLECTABLE_CARDS` 仍由 collector 门面命名空间再导出，补丁点不变。

### 3.3 思路

- 三个模块各自承担大量职责，单文件行数已到 1000–3400 行，后续实机问题定位、
  专项测试与功能扩展都变得困难；`_use_action` 单个状态机 335 行/圈复杂度 73，
  任何一次“恢复/对账/点击”组合改动都容易顾此失彼。
- 拆分采用“门面 + mixin”模式而非重写：`Navigator` / `Trader` / `Collector` 类与
  全部对外导出符号保留在门面，方法按职责移动到对应 mixin，常量与数据类下沉到
  `*_constants` 并由门面 `# noqa: F401` 再导出。这样所有 `from ... import` 调用面不变，
  外部代码与测试无需改导入（只有补丁点需要跟随 `monotonic` 的实际所在模块迁移）。
- 移动全部由脚本机械完成（`.local-dev/stage3_split_*.py`），从备份原始文件按行切片、
  统一缩进，避免手抄漏行；移动后做 AST 语句级比对确认逐语句一致。
- `_use_action` 按“状态分支”而不是按“代码行数”切分：先查已有记录 → 探测图标 →
  恢复未决意图 → 缺图/未知 → USED 对账 → AVAILABLE 点击 → 反馈结算。
  每个阶段方法只负责一个可独立验证的判定；`_prepare_before_click` 用返回元组
  把“失败结果”与“点击前计数”分离，入口保持线性可读。
- 行为等价由三层保证：AST 语句级比对（四个逐字移动块完全一致；
  `_prepare_before_click` 仅按设计做返回元组包装）、554 项完整测试、
  独立只读子代理审阅。

### 3.4 验证与审阅

- 554 项完整单元测试（`-v`）、Ruff、compileall、`git diff --check` 与 src 键盘限制
  扫描全部通过；无 TODO/FIXME/XXX/HACK 残留；无重复方法定义、无类名自引用残留。
- `_use_action` 圈复杂度由 73 降至 7；门面对所有真实外部消费者的导出符号完整
  （AST 核对 src/tests 全部 `from ... import` 零缺失）。
- 独立只读子代理按五项验收标准（门面导出、行为等价、测试、门禁、文档）复核后
  裁决「PASS（可合入，无阻塞项）」，并确认常量与数据类逐值迁移、方法级 AST 一致、
  依赖方向无循环导入。
- 非阻塞文档建议已修正：collector 旧行数为 1381（非 git diff 的 1043）；
  文档措辞由“两阶段”改为“三阶段”。
- 派发机制说明：顶层审阅代理 `review_stage3` 中途被中断，但其子代理完成报告并回传
  PASS；主线程另行独立复核关键项（语句等价、符号完整性、复杂度、门禁），结论一致。

### 3.5 提交与备份

- 本阶段代码与测试已提交为 `22b7ae7 refactor(map-trade): 拆分 navigator/trader/collector
  大模块与采集状态机`（15 文件，+7163/−5859）；本文档提交为
  `6faa0fe docs(refactor): 补充三阶段重构大纲、主要内容与思路`；均未推送。
- 备份位于 `D:\ok-bd2-backups\ok-bd2-main-<新提交哈希>-<时间戳>\`（含完整历史 bundle、
  HEAD 快照 zip、SHA256 与提交清单）。

---

## 四、阶段四：统一输入与坐标模型

### 4.1 大纲

- 目标：把 MapTradeTask / PVPTask / SquareGoddessTask 三处重复的拖拽/滚轮实现
  收敛为 `BaseBD2Task` 公开方法；把 1280×720、1920×1080、2560×1440 三套参考分辨率
  统一为显式 `ReferenceCalibration`；技能槽坐标以 `action_icons.py` 为唯一事实来源；
  把基类中的 PVP 特殊页逻辑下沉到 `PVPTask`。
- 范围：`BaseBD2Task`、`PVPTask`、`SquareGoddessTask`、`MapTradeTask`、
  `map_trade/` 各模块、7 个任务模块与对应测试。
- 状态：本地未提交，待独立审阅与用户指示。

### 4.2 主要内容

- 新增 `src/utils/calibration.py`：
  - `ReferenceCalibration`（width / height / size）与 `HD_720` / `FHD_1080` /
    `QHD_1440` 三个显式校准对象。
  - PVP / Square 的 `REFERENCE_WIDTH/HEIGHT`、`MFABD2_*`、`ENTRY_*`，
    Daily / FreeGacha / QuickSuppression / BargainLevel / BD2InputTest /
    AutoLogin 的 1920×1080 参考常量全部改为从校准对象派生。
- `map_trade` 参考分辨率：
  - `models.py` 删除 `MF_REFERENCE_WIDTH/HEIGHT`，新增 `MAP_TRADE_REFERENCE = HD_720`。
  - `vision.py` 的 `reference_point` / `reference_roi` / `click_reference` /
    模板 ROI 引用全部改用 `MAP_TRADE_REFERENCE`。
- 技能槽坐标单一事实来源：
  - `action_icons.py` 新增 `SKILL_GROUP_CENTERS_REFERENCE`
    （1/2/3 组中心 `(1671, 1011)` / `(1749, 1011)` / `(1824, 1011)`）。
  - `collector_constants.SKILL_GROUP_REFERENCE_POINTS` 直接复用同一字典对象；
    `navigator_constants` 槽位 1/2 与传送技能中心改从 `action_icons` 派生。
  - 由此修正三处分叉：槽位 1 `(1672,1010)→(1671,1011)`、槽位 2
    `(1748,1010)→(1749,1011)`、传送 `(1793,789)→(1795,788)`，均收敛到
    `action_icons` 中经实机标定的既有值。
- 输入统一：
  - `BaseBD2Task` 新增公开 `drag_client()` / `scroll_client()`（自 MapTradeTask
    逐字收敛）。
  - 删除 MapTradeTask / PVPTask / SquareGoddessTask 各自的 `_drag_client`、
    `_scroll_client`，以及 PVPTask 未使用的 `_post_drag_client`。
  - `navigator_story.py` / `trader_cartridge.py` / `vision.py` 改用公开方法。
- PVP 特殊页逻辑下沉：
  - `BaseBD2Task` 删除最近卡带 PVP 模板匹配、赛季奖励/晋级/段位下滑 OCR、
    周一判定与相关常量；`_recent_cartridge_is_pvp` 改为默认返回 False 的钩子。
  - `PVPTask` 接收上述全部逻辑与常量，并在 `__init__` 初始化模板缓存字段；
    `open_cartridge_quick_switcher` 只在该钩子返回 True 时调用特殊页处理。

### 4.3 思路

- 三份拖拽/滚轮实现源自 MapTradeTask 向 PVP / Square 的复制，任何交互修复都需
  三处同步；PVP 还残留一份未被调用的 post-message 实现。收敛到基类后输入语义只有
  一份，测试直接锁定“方法只存在于基类”。
- 参考分辨率此前散落为裸数字，任务模块各自重复定义；显式校准对象 + 派生常量让
  “每个任务以哪个分辨率标定”成为可测试的事实，避免再出现 1920/1080 与 1280/720
  手抄错位。
- 技能组按钮中心此前在 action_icons（技能栏）、collector_constants（技能组恢复）、
  navigator_constants（箱庭技能组切换）三处独立标定，存在 1–2 像素分叉；统一到
  `action_icons` 后按既有实机标定收敛，像素级变化属于修正而非回归。
- PVP 特殊页逻辑挂在基类，导致所有使用 `open_cartridge_quick_switcher` 的任务
  （Square / MapTrade）都隐式执行 PVP 模板匹配与特殊页 OCR。按职责下沉后，非 PVP
  任务不再做 PVP 识别与特殊页处理——这是本阶段有意的职责隔离。

### 4.4 验证与审阅

- 562 项完整单元测试（554 + 新增 8 项 `test_calibration`）、Ruff、compileall、
  `git diff --check` 与 src 键盘限制扫描全部通过。
- 新增 `test_calibration.py` 锁定：校准常量、任务模块派生、Vision 720p 参考、
  技能坐标单一来源、输入方法归属、PVP 职责归属（基类无任何特殊页方法残留）。
- 测试补丁点迁移：`test_pvp_task.py` 的 `monotonic` patch 改到 `PVPTask` 模块、
  `_drag_client` → `drag_client`；`test_map_trade.py` 的 `_scroll_client` →
  `scroll_client`（7 处）。
- 独立审阅补充：审阅发现 `AutoLoginTask` 的 1920×1080 参考常量仍为裸数字
  （本阶段初版遗漏的任务模块），已按同一方式改为从 `FHD_1080` 派生并纳入
  `test_calibration` 回归；数值不变，仅派生方式统一。
- 独立审阅结论：PASS（无阻塞项）。按验收清单逐项复核：旧输入接口与旧坐标
  分叉无残留；校准对象与各任务派生一致；技能坐标单一事实来源成立；PVP 特殊页
  逻辑完全下沉且基类只剩默认 False 钩子；AST 比对确认 8 个下沉方法与输入方法
  为逐字搬迁（输入方法仅公开化改名）；562 项测试、Ruff、compileall、
  `git diff --check`、键盘扫描全绿；改动范围与文档一致。两点非阻塞观察：
  （1）本阶段初版遗漏 AutoLoginTask，已在审阅中补全；（2）审阅子代理消息机制
  再次异常（嵌套派发/空转），最终结论由主线程按同一清单独立复核收口。

### 4.5 提交与备份

- 本阶段代码与测试已提交为 `aaf6a0a refactor(input): 统一输入与坐标模型并下沉PVP特殊页逻辑`；
  本文档单独提交（未推送）。
- 备份位于 `D:\ok-bd2-backups\ok-bd2-main-<新提交哈希>-<时间戳>\`（含完整历史 bundle、
  HEAD 快照 zip、SHA256 与提交清单），具体路径与哈希以 AGENTS.md 当前计划区为准。

---

## 五、阶段五：错误语义与性能修复

### 5.1 大纲

- 目标：修正采集额度超限的错误语义、移除跑商盲点点击、降低共享界面分类的 OCR
  成本，并将 QuickHuntTask 从“继承 DailyTask + 反向删配置”改为组合式共享 mixin。
- 范围：`map_trade/progress.py`、`map_trade/collector.py`、
  `map_trade/navigator.py`、`map_trade/navigator_trade.py`、
  `map_trade/navigator_constants.py`，以及
  `DailyTask.py` / `QuickHuntTask.py` 与新拆分
  `task_vision_mixin.py` / `quick_hunt.py`。
- 提交：`09d36ed refactor(tasks): 错误语义与分类性能修复并组合化快速狩猎`
  （12 文件，+2179/−1659）。

### 5.2 主要内容

- 额度超限失败语义：
  - `ProgressStore.mark_target` 在吸收/召集/压制额度超限时不再 raise
    `RuntimeError`，改为持久化 `depleted_today` 并返回 False；重复目标同样
    返回 False，docstring 明确两种 False 语义。
  - `Collector.run` 统一把内部 `RuntimeError` 转成失败 `CollectionResult`
    （日志保留异常文本），任务编排不再因点击等中断异常直接炸出。
  - 三处 `mark_target` 调用检查返回值；False 时返回
    `CollectionResult(True, depleted=True, ...)`，已成功小图计数不丢失，
    与既有“实机技能次数到上限”分支保持一致。
- 跑商盲点移除：
  - `_bargain_and_enter_shop` 删除“点击成功但商店页 OCR 未确认仍按成功继续”
    与“对话动画偶尔需要一次中心无害点击”两条固定点/宽松路径；改为
    `_click_shop_entry_with_retries()` 前置 OCR 识别（最多 3 次、间隔 0.5 秒），
    失败返回明确失败；点击成功后 `wait_trade_state` 必须确认 SHOP。
  - `_return_home_from_discount_shop` 的关闭按钮与箱庭主页按钮从固定点盲点
    改为模板优先助手（关闭按钮 2 个模板、主页按钮 3 个模板，门槛 0.85，
    相对 ROI 0–0.2×0–0.18 与 0.86–1×0–0.18），模板未通过时才回退到已标定
    相对点 (82,36) / (1797,63)；确认弹窗点击后的 0.8 秒时序保持与旧实现一致。
- 界面分类性能：
  - `classify` / `classify_trade` 从整帧 OCR 改为 7 个局部 ROI OCR（加载、
    商店页签、商店标题、传送阵标题、卡带标题、卡带类别、料理标题/材料），
    坐标全部为 1920×1080 参考像素派生相对 ROI，实机 OCR 报告见
    `.local-dev/experiments/classify-roi-20260810/ocr_report.json`。
  - 两个入口记录每次分类的 `monotonic()` 耗时并输出
    “界面分类耗时”/“跑商界面分类耗时”，便于后续量化。
  - 信号隔离保持不变：共享 `classify` 仍不调用商人模板；商人模板只属于
    `classify_trade`。
- QuickHuntTask 组合化：
  - 新增 `src/tasks/task_vision_mixin.py`：共享视觉状态、模板/OCR/等待 helper
    与主页确认信号；`DailyTask` 与 `QuickHuntTask` 共同使用。
  - 新增 `src/tasks/quick_hunt.py`：`QUICK_HUNT_*` 常量、
    `QuickHuntConfigMixin`（默认配置/描述/type 配置/安装）与
    `QuickHuntFeatureMixin`（全部快速狩猎方法）。
  - `DailyTask` 裁掉快速狩猎与共享视觉实现，仅保留公会/小屋/收菜日常行为；
    `QuickHuntTask` 不再继承 `DailyTask`，改为组合三个 mixin + `BaseBD2Task`。
  - 关键修复：type 配置回调含 `self._queue_quick_hunt_test` lambda，不能做类
    属性，因此 `quick_hunt_config_type` 改为实例方法 `_quick_hunt_type_config()`。

### 5.3 思路

- mark_target 抛异常把“今日额度用尽”与“流程异常”混为一谈，任务层无法区分；
  改为返回值后，额度耗尽成为可计划、可持久化的正常终止，异常只保留给真正
  不可恢复的流程错误（`Collector.run` 统一兜底成失败结果）。
- 商店入口与折扣商店关闭/主页按钮都属于“能识别目标时优先点击识别结果中心”
  的场景；固定点只能作为模板门禁失败后的兜底。实机截图探测显示关闭/主页
  候选模板最高分仅约 0.62，因此保留严格门槛（0.85）下的标定相对点回退，
  而不是硬性只点模板。
- 整帧 OCR 是共享分类的主要成本来源，而各状态关键字只出现在固定 UI 区域；
  按状态切局部 ROI 可显著减少 OCR 像素量，同时用耗时日志暴露每次分类的成本
  变化，便于实机验证。
- QuickHuntTask 之前靠“继承 DailyTask 再删除日常配置键”实现独立，代价是
  DailyTask 的修改总会波及快速狩猎，且反向删除顺序脆弱；组合式 mixin 让
  职责边界显式化，视觉 helper 与快速狩猎能力各自独立可复用。

### 5.4 验证与审阅

- 565 项完整单元测试（564 + 新增 Collector.run 异常转换回归）、Ruff、
  compileall、`git diff --check` 与 src 键盘限制扫描全部通过。
- 测试更新：`test_map_trade.py` 的商店入口失败语义、盲点零点击、
  局部 ROI OCR 分类、return_home 模板优先回退时序、额度超限 False 语义；
  `test_daily_task.py` / `test_calibration.py` 的常量导入迁移到
  `task_vision_mixin` / `quick_hunt`。
- AST 比对：原 DailyTask 46 个模块级名称与 60 个方法全部存在于新四模块
  union，仅新增三个意图内方法（`_init_vision_state` /
  `_install_quick_hunt_config` / `_quick_hunt_type_config`）。
- 独立只读子代理审阅结论与主线程同清单复核：PASS（无阻塞项）；一处非阻塞
  观察为 `TaskVisionMixin._ocr_text` 仍按旧实现硬编码读取“日常 OCR 阈值”，
  QuickHuntTask 的“快速狩猎 OCR 阈值”配置暂不生效（历史遗留，本次为逐字
  搬迁，未扩大行为差异）。

### 5.5 提交与备份

- 本阶段代码与测试已提交为 `09d36ed`；本文档单独提交（未推送）。
- 备份位于 `D:\ok-bd2-backups\ok-bd2-main-<新提交哈希>-<时间戳>\`
  （含完整历史 bundle、HEAD 快照 zip、SHA256 与提交清单），具体路径与哈希
  以 AGENTS.md 当前计划区为准。

---

## 六、阶段六：仓库与测试卫生

### 6.1 大纲

- 目标：降低超大测试文件的维护成本，把探针/诊断能力与正式任务注册隔离，
  消除源码包版本漂移，并把本地大目录、worktree、旧分支整理为可确认的清理清单。
- 范围：`tests/test_map_trade.py` 及拆分测试、`src/config.py`、
  `src/tasks/debug_registry.py`、`main_debug.py`、`src/ui/BD2StatusTab.py`、
  版本一致性测试、README/发布文档与 AGENTS.md 计划区。
- 边界：本阶段只盘点本地清理项，不删除目录、worktree 或分支；提交和推送仅在
  用户本次明确授权后执行，仍不备份、不打标签、不发布。

### 6.2 主要内容

- map-trade 测试拆分：
  - 删除单体 `tests/test_map_trade.py`，按 action-icons、calendar、card-status、
    collection、collector、config、navigator、progress、safety、sandbox、shop、
    trader、vision 共 13 个职责模块拆分。
  - `FakeTask`、`_seed_action_records`、`_seed_battle_supplements` 移到
    `tests/helpers/map_trade.py`；HEAD 基线与初始拆分均为 279 项。
  - 原 279 个测试名零缺失/零新增/零重复，测试函数体及核心 helper 的 AST 与基线
    等价；后续 Meta-Review 新增 3 项 ROI 回归，当前 map-trade 共 282 项。
- 调试任务隔离：
  - BD2ProbeTask、BD2MapCollectionProbeTask、BD2OneTimeTask、BD2DiagnosisTask
    从正式 `config["onetime_tasks"]` 移除。
  - 新增 `src/tasks/debug_registry.py`，由 `main_debug.py` 幂等安装四个调试任务；
    正式 `main.py` 不加载该注册表。
  - BD2StatusTab 的“运行基础检查”优先复用调试实例；正式入口没有注册实例时，
    按框架生命周期创建、`after_init`、`post_init`，app/executor 未就绪则明确失败。
- 版本来源统一：
  - `pyproject.toml` 的 `0.1.1` 是源码包版本唯一来源；不借重构发布新版本。
  - `src.config.runtime_version()` 在源码树读取 `pyproject.toml`；
    `src/config.py` 只保留 PyAppify 内联工具可替换的 release-tag marker，供不含
    `pyproject.toml` 的生成更新仓库回退。
  - 新增四项回归，锁定源码一致性、marker 隔离、无 pyproject fallback 与
    现有 inline 正则兼容；README 和发布文档区分包版本与交付 tag。
- Meta-Review 回归修复：
  - 新增 reference XYWH 到 relative LTRB 的集中转换，修正七组分类 OCR ROI 与三个
    章节主页模板区域；720p、1080p、1440p、4K 边界和自包含裁剪分类均有回归覆盖。
  - QuickHunt 显式恢复主页亮度与确认等待配置的默认值、说明和数值类型，旧持久化值
    经 `Config.verify_config()` 后保持不变。
  - PyAppify 文档明确源码 marker 经 inline 流程替换为生成仓库 tag，且仅在生成内容
    不含 `pyproject.toml` 时使用该 tag 回退。
- 计划与清理盘点：
  - AGENTS.md 中阶段一至五和两项历史完成记录压缩为单行结论；待实机验证、
    待发版和待清理确认事项继续保留。
  - 本地目录实测：`.local-dev` 8.19 GiB、`probe_outputs` 0.27 GiB、
    `pyappify_build` 2.29 GiB、`pyappify_dist` 1.90 GiB。
  - 三个附加 worktree 中 dependency-runtime-update 与 wt-sell 干净；
    payload-282920b 有 `requirements.txt`、`src/config.py` 修改和未跟踪 `pyappify/`，
    禁止直接清理。六条旧本地分支均已合入 main，但删除仍需用户确认。

### 6.3 思路

- 测试拆分只移动测试与共享 fixture，不改测试逻辑；用测试名集合、函数 AST 和
  unittest discover 三层证据避免“文件拆开了，但断言或收集范围变了”。
- 调试任务不应随正式 `onetime_tasks` 发布，但状态页的基础检查是正式 UI 能力；
  因此把注册隔离与按需实例化分开，既缩小正式任务面，又保留该按钮。
- 包版本与更新交付 tag 是两种语义：源码运行时由 pyproject 提供包版本，生成的
  PyAppify 更新仓库不携带 pyproject，继续由既有 inline 流程写入 tag marker。
- 本地清理不可逆且 payload worktree 含未提交数据，因此阶段六只提供精确清单，
  不把“仓库卫生”解释为自动删除用户资产。

### 6.4 验证与审阅

- 初始阶段六 575 项完整单元测试通过（565 项原套件 + 6 项调试注册回归 + 4 项版本
  契约回归）；Meta-Review 随后新增 5 项回归，最终 580 项全部通过。
- map-trade 的原 279 项在拆分前后保持等价；加入 3 项 ROI 回归后当前为 282 项。
- Ruff、compileall、`git diff --check` 与 src 键盘实现扫描全部通过。
- 三个实现 worker 分片均完成；随后由三个不同的只读 verifier 分别检查测试拆分、
  调试注册、版本/文档与全局门禁，共 24/24 条验收标准 PASS，无阻塞项。
- 非阻塞建议：未来可补一个显式的 null-executor 状态页测试和一个 main_debug wiring
  测试；当前行为已由直接调用、注册幂等测试和 575 项全量门禁覆盖。

### 6.5 提交与备份

- 阶段六与 Meta-Review 修复随本次用户授权提交并推送；未备份、未打标签、未发布。
- 本地目录/worktree/分支均未删除，等待用户按清单明确确认。

---

## 七、验证门禁

每次修改后完整执行：

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m ruff check src tests
.\.venv\Scripts\python.exe -m compileall -q src tests
git diff --check
```

另按 AGENTS.md 约束扫描 `src`，不得出现项目自定义键盘发送/按下/释放/热键注册/键盘映射。

---

## 八、维护注意事项与后续计划

- 门面文件只保留类、编排入口与再导出；新增逻辑优先落在对应职责的 mixin/子模块，
  避免门面重新膨胀。
- `*_constants` 是常量与数据类唯一归属地；新增常量先放 constants，再由门面再导出。
- 测试补丁点必须跟随被补丁符号的实际所在模块；移动代码后先用 AST/全量测试确认，
  再迁移补丁路径。
- `_use_action` 的阶段方法分别对应可独立验证的状态分支；新增分支时保持入口编排
  线性可读，不要回到单函数大状态机。
- 拖拽/滚轮只允许调用 `BaseBD2Task.drag_client/scroll_client`；新增任务不得复制
  私有输入实现。
- 参考分辨率一律使用 `src/utils/calibration.py` 的校准对象派生；技能槽/技能组中心
  只允许以 `action_icons.py` 为唯一事实来源。
- PVP 特殊页（最近卡带模板、赛季奖励/晋级/段位下滑）只属于 `PVPTask`；共享快速
  切换流程通过 `_recent_cartridge_is_pvp` 钩子决定是否启用。
- 额度超限是 `mark_target` 的正常 False 结果（并持久化 `depleted_today`），
  不是异常；`Collector.run` 对外只返回 `CollectionResult`，不向编排层抛
  `RuntimeError`。
- 商店入口/折扣商店关闭/主页按钮遵循“识别优先、固定点兜底”；模板新增时
  保持严格门槛，不要退回裸固定点盲点。
- 共享界面分类只允许局部 ROI OCR；新增界面状态时先在实机截图确认关键字
  所在参考区域，再派生相对 ROI。
- 快速狩猎能力统一在 `quick_hunt.py` 的 mixin 中；`DailyTask` 不再承载
  快速狩猎逻辑，新增任务按需组合 `QuickHuntFeatureMixin`。
- map-trade 测试新增用例时放入对应职责文件；跨 collector/progress/vision 的共享替身
  统一维护在 `tests/helpers/map_trade.py`，不要重新建立单体 `test_map_trade.py`。
- 探针、诊断与基础检查任务只通过 `src/tasks/debug_registry.py` 进入 `main_debug.py`；
  新增调试任务不得直接放回正式 `config["onetime_tasks"]`。
- 源码包版本只修改 `pyproject.toml`；`src/config.py` 的 release marker 是生成更新仓库
  的交付 tag 接缝，不得手工同步为第二份包版本。
- `.local-dev`、probe/build/dist 目录、worktree 和已合入分支的清理必须先复查工作区状态
  并取得用户确认；特别是 payload-282920b 当前含未提交内容。
- 阶段四包含两处有意的行为变化（非回归）：技能组/传送中心按实机标定收敛 1–2 像素；
  Square / MapTrade 不再隐式执行 PVP 模板匹配与特殊页处理。
- 阶段三/四/五的实机行为差异（额度耗尽终止、跑商入口失败、分类 ROI、快速狩猎
  组合化）以测试锁定，实机验证与发版门禁继续按 AGENTS.md 当前计划执行
  （收到明确发布请求后再推送、打标签与发布）。
