# ok-bd2 重构记录（阶段一 / 阶段二）

本文档用于管理与后期维护，记录 2026-08-10 两阶段重构的动机、改动范围、提交、审阅结论、
验证门禁与后续注意事项。代码以 `main` 分支为准。

## 一、阶段一：摘除测试兼容层

- 目标：删除 `Collector` / `ProgressStore` / `Navigator` / `Trader` 中长期保留的
  `durable_actions` 与 `formal` 双轨、旧参数回退、测试替身接缝，让正式路径成为唯一路径，
  降低后续修改的心智负担。
- 主要改动：
  - `Collector`：`_use_action` / `_use_actions` / `_start_search` 改为必填
    `card_id` + `map_role`，正式账本路径唯一；删除 `_read_count_window` 旧两参回退与
    `_use_skills` 兼容 helper；技能组 1 恢复不再区分卡带上下文（调用均带角色）。
  - `ProgressStore`：删除 `require_actions` 参数及 `action_record` / `reserve_action` /
    `mark_clicked` / `mark_local_done` / `settle_pending_actions` 别名；
    `mark_target` 一律要求 durable 动作记录。
  - `Navigator`：删除 `MAP_LEFT/RIGHT_TEMPLATE`、`AREA_MAP_TITLE_OCR_*_ROI` 兼容常量、
    `locate_story_card_for_probe`、`_area_map_teleports`、
    `open_teleport_map_from_sandbox(map_open_point)` 参数与 `del map_open_point`、
    `vision.template_hsv_color_ratios` 的 `getattr` 测试替身回退。
  - `BD2MapCollectionProbeTask`：改用 `locate_probe_story_card` 返回值语义
    （None = 未找到）。
  - `Trader`：删除 `_confirm_sale` / `_locate_sale_item` / `_wait_sale_item_point`
    兼容包装，统一走多候选接口。
  - 测试同步迁移到公共/正式接口，554 项完整测试通过。
- 提交：`6dc8b58 refactor(map-trade): 摘除测试兼容层并统一正式接口`（7 文件，+330/−339）。
- 审阅：独立子代理按六项验收标准裁决「可合入，无阻塞项」。

## 二、阶段二：统一视觉识别管线

- 目标：消除 DailyTask / FreeGachaTask / PVPTask / SquareGoddessTask / AutoLoginTask
  五份各自重复的模板匹配实现（TemplateSpec / MatchResult 定义、
  `_load_template` / `_read_template_and_mask` / `_match` / `_passes` /
  `_home_brightness_ratio_for_template`），统一为共享引擎，并修复
  `map_trade/vision.py` 对任务层的反向依赖。
- 主要改动：
  - 新增共享规格与引擎：`src/utils/vision_models.py`（TemplateSpec / MatchResult /
    EMPTY_MATCH）、`src/utils/task_vision.py`（load_template / match_template /
    passes_match / brightness_ratio / resolve_match_threshold）。
  - `src/utils/image_utils.py`：`green_mask_from_template` 从 `BaseBD2Task` 下沉至此；
    `BaseBD2Task` 反向再导入，依赖方向单向正确（utils 不导入 src.tasks）。
  - `src/tasks/map_trade/vision.py`：`Vision.match / passes / _load` 委托共享引擎，
    保留 1280×720 ROI 参考、`threshold_for` 钳制与测试 loader 接缝。
  - 五个任务删除各自 dataclass 与整段匹配/加载/亮度实现，改为薄封装（缺失模板日志、
    内存错误暂停、亮度中心差异保留）。
  - 测试迁移到共享类型与共享引擎补丁点（image_utils / template_resolution /
    task_vision）。
- 提交：`0656264 refactor(vision): 统一视觉识别管线并去除五份重复实现`
  （16 文件，+666/−1134，净删约 773 行）。
- 审阅：独立子代理裁决「PASS（可合入，无阻塞项）」，主线程按同一清单独立复核一致。

### 阶段二审阅要点

- 行为等价：min_size（map_trade 4 / PVP、Square 5 / Daily、Gacha、AutoLogin 8）、
  搜索区（Main* 走主区、PVP/Square 1920 参考、Vision 1280×720 参考）、
  `candidate_threshold` 仅用于匹配、`minimum_safe_threshold` 同时作用于匹配与通过判定、
  亮度中心（Daily 166/158、PVP/Square 222/211、AutoLogin 按百分比配置）均与旧实现一致。
- 残留扫描：旧私有符号（`DailyTemplateSpec`、`_read_template_and_mask`、
  `_candidate_template_threshold` 等）在 src/tests 零命中。
- 依赖方向：`src/utils` 无 `src.tasks` 导入；`map_trade/vision.py` 不再引用
  `BaseBD2Task` 的 green_mask。
- 非阻塞观察（当前无实际影响，未来维护需注意）：
  1. 绿幕容差统一为严格 0（Daily/AutoLogin 旧实现为 ±4）。实测全部绿幕资产
     两种容差掩码差异为 0；新增含近绿像素的模板时需留意。
  2. 非绿色 RGBA 模板现在会生成 alpha 遮罩（旧 Daily/AutoLogin 忽略 alpha）。
     实测当前资产 alpha 全为 255，最终归并为无遮罩，无差异。
  3. 共享引擎缓存键为 `spec.name`（旧 map_trade 用 `file_name`），当前无行为影响。
  4. 共享 `reference_roi_frame` 无宽高 ≥1 下限钳制，仅在 ROI 缩放到 0 的退化帧下
     才可能与旧 `Vision.reference_roi` 不同。

## 三、文档与提交状态

- 本文档提交：`docs(refactor): 记录阶段一/二重构说明与维护要点`。
- 本地 `main` 领先 `origin/main` 的两笔重构提交（`6dc8b58`、`0656264`）与本文档提交
  已推送并备份。
- 备份位置：`D:\ok-bd2-backups\ok-bd2-main-<短哈希>-<时间戳>\`（含完整历史 bundle、
  HEAD 快照 zip、SHA256 与提交清单）。

## 四、验证门禁

每次修改后完整执行：

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests
.\.venv\Scripts\python.exe -m ruff check src tests
.\.venv\Scripts\python.exe -m compileall -q src tests
git diff --check
```

另按 AGENTS.md 约束扫描 `src`，不得出现项目自定义键盘发送/按下/释放/热键注册/键盘映射。

## 五、后续计划

- 阶段三：拆分 `navigator.py` / `trader.py` / `collector.py` 大模块与状态机。
- 涉及大模块拆分时，先更新本文档的改动范围与验证结果，再进入提交流程。
