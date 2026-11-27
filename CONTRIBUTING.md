# 贡献指南 / Contributing to ok-bd2

感谢你对 `ok-bd2` 的兴趣！无论是修复 Bug、优化识别稳定性、补充文档，还是改进桌面端体验，都非常欢迎你的参与。

提交代码或发起 Pull Request 前，请花几分钟阅读本指南。

## 1. 开发环境准备

### 基础要求

- **操作系统**：Windows 10 / Windows 11（实机游戏测试与打包均基于 Windows）
- **Python**：`3.12`（项目依赖以 `pyproject.toml` 与 `uv.lock` 为唯一锁定来源）
- **包管理工具**：推荐安装 [`uv`](https://docs.astral.sh/uv/)
- **Node.js**（仅修改 `desktop/` 桌面端 UI 或 Electron 壳层时需要）：`>= 24`

### 克隆与安装依赖

```powershell
git clone https://github.com/GodRaymond233/ok-bd2.git
cd ok-bd2

# 方式一：使用 uv 创建并同步开发环境（推荐）
uv sync

# 方式二：使用 venv + 导出的开发依赖文件
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python scripts/install_dependencies.py requirements-dev.txt
```

如需开发或测试桌面端前端/壳层：

```powershell
# React + Vite 前端页面
cd desktop/ui
npm ci
npm run check
npm test

# Electron 壳层
cd ../shell
npm ci
npm run check
npm test
```

## 2. 项目目录导览

| 路径 | 职责 |
|---|---|
| `src/` | Python 核心代码：任务状态机（`src/tasks/`）、视觉与 OCR 工具（`src/utils/`）、窗口交互（`src/interaction/`）、诊断报告（`src/diagnostics/`）与本地服务（`src/desktop/`） |
| `desktop/ui/` | React 19 + TypeScript + Vite 桌面端页面 |
| `desktop/shell/` | Electron 壳层，负责宿主窗口与 Python 核心进程托管 |
| `tests/` | Python 单元测试套件 |
| `docs/` | MkDocs Material 在线文档源码 |
| `scripts/` | 质量门禁、依赖刷新、构建与发布辅助脚本 |
| `recognition-assets/` | 模板与图像识别资源 |

更详细的架构分层与模块依赖规则请查阅 [docs/architecture.md](docs/architecture.md)。

## 3. 核心工程约束（必读）

为保证 BrownDust II PC 端实机运行的稳定性与可维护性，所有贡献必须遵守以下硬性约束：

1. **禁止自动化键盘输入**：BrownDust II PC 客户端无法可靠接收自动化键盘输入。`src/` 中不得引入 `send_key`、`pydirectinput`、`pynput.keyboard`、热键注册或键盘映射等实现；游戏交互一律使用鼠标点击、滚轮或拖动。
2. **使用相对比例坐标**：能识别目标时优先点击识别结果中心；仅在稳定固定 UI 位置使用相对比例坐标（0–1），禁止写死单一分辨率的像素坐标。
3. **箱庭确认信号隔离**：剧情卡带箱庭、跑商商人场景与 PVP 箱庭是三种独立上下文，不得在通用分类器中混用各自专用模板。
4. **不要手工编辑 `requirements*.txt`**：所有 Python 依赖变更都应修改 `pyproject.toml`，然后运行：
   ```powershell
   .\scripts\refresh_dependencies.ps1
   .\scripts\check_dependency_exports.ps1
   ```

## 4. 本地测试与质量门禁

修改过程中可只运行直接受影响的测试模块，最终提交前请执行完整门禁：

```powershell
# 开发过程中快速验证单个测试模块
.\scripts\run_checks.ps1 -Mode Focused -Tests tests.test_pvp_task

# 提交前执行完整质量门禁（Ruff、compileall、全量单测、git diff 空白检查、键盘限制扫描）
.\scripts\run_checks.ps1 -Mode Final

# 如涉及 desktop/ 前端或 Electron 壳层改动，追加桌面端类型检查、单测与构建
.\scripts\run_checks.ps1 -Mode Final -IncludeDesktop

# 涉及依赖变更或发布前额外校验锁文件与导出一致性
.\scripts\run_checks.ps1 -Mode Release
```

如修改了 `docs/` 或 `mkdocs.yml`，请顺手确认文档严格构建通过：

```powershell
python -m mkdocs build --strict
```

## 5. Issue 提交与分诊规范

1. **优先使用表单模板**：提交 Bug 或功能建议时，请使用仓库内置的 [Bug 报告](https://github.com/GodRaymond233/ok-bd2/issues/new?template=bug_report.yml) 或 [功能建议](https://github.com/GodRaymond233/ok-bd2/issues/new?template=feature_request.yml) 表单，标题尽量简明写清「模块 + 现象」（例如 `[Bug]: 跑商界面无法识别砍价按钮`）。
2. **附带问题报告**：遇到任务报错或识别异常时，优先在程序首页点击 **生成问题报告**，在 Issue 中填写 `RPT-*` 编号并附上脱敏 ZIP。
3. **分诊与生命周期**：
   - 缺少复现步骤、分辨率或问题报告的 Issue 会标记 `status: needs-info`；若超过 **14 天**未补充关键信息，维护者会礼貌关闭（补充信息后可随时重开）。
   - 重复提交的 Issue 会关联主 Issue 并标记 `duplicate` 后关闭；已随新版本解决的需求或缺陷会在对应版本发布后关联关闭。

## 6. Pull Request（PR）规范

### 6.1 PR 标题格式

仓库启用了轻量 PR 标题校验（[pr-title.yml](.github/workflows/pr-title.yml)），统一采用 Conventional Commits 前缀，**描述部分支持中文或英文**：

```text
<type>(<scope>): <简明描述>
```

- **常用 `type`**：
  - `feat`：新功能或新任务
  - `fix`：Bug 修复或识别容错修复
  - `refactor`：不改变外部行为的代码重构
  - `perf`：性能优化
  - `docs`：文档、模板或注释更新
  - `test`：测试补充或修正
  - `ci` / `build`：工作流、打包或依赖构建调整
  - `chore`：杂项维护
- **推荐 `scope`**：对应模块名，例如 `daily`、`pvp`、`trade`、`collection`、`glutalk`、`equipment`、`quick-hunt`、`desktop`、`vision`、`docs`、`ci`（范围不明显时可省略 `(<scope>)`）。
- **示例**：
  - `fix(glutalk): 跳过全屏相遇并关闭满好感约会奖励页`
  - `fix(quick-hunt): 过滤圣石库存 OCR 中的杂散非数字字符`
  - `feat(desktop): 新增任务执行耗时汇总卡片`
  - `docs: 补充 PR 规范与标签说明`

### 6.2 PR 粒度、审阅与合入策略

1. **单一职责**：一个 PR 只解决一个明确问题或实现一项独立功能，避免把多个不相关改动打包进同一个 PR。
2. **大改动先对齐**：涉及公共基类、核心架构调整、跨模块重构或引入新依赖前，请先开 [Feature Request](https://github.com/GodRaymond233/ok-bd2/issues/new?template=feature_request.yml) 沟通方案，避免返工。
3. **分支与提交卫生**：从 `main` 切出清晰命名的分支（如 `fix/pvp-entry-retry`、`feat/trade-summary`），不混入本地缓存、日志、截图或无关改动；CI 会执行 `scripts/check_commit_attribution.py`，建议启用本地 hook：
   ```powershell
   git config core.hooksPath .githooks
   ```
4. **验证举证**：
   - 必须通过 CI 门禁（`.\scripts\run_checks.ps1 -Mode Final`；如涉及 `desktop/` 需同步通过前端与壳层检查）。
   - 涉及模板匹配、OCR 或坐标逻辑的 PR，请在 PR 描述中说明验证所用的分辨率、原图回放或单测覆盖情况。
5. **合入方式**：外部 PR 默认采用 **Squash and merge** 合入 `main`，最终提交信息以规范化后的 PR 标题为准，贡献者无需为中间过程提交反复手工 rebase。

## 7. Label 体系说明

仓库使用三组核心标签协助分类与分诊：

| 维度 | 标签 | 含义 |
|---|---|---|
| **类型** | `bug` / `enhancement` / `documentation` / `question` | 缺陷、功能建议、文档改进、使用咨询 |
| **状态** | `status: needs-info` / `status: confirmed` / `status: awaiting-verification` | 待补充信息 / 已确认待修 / 已修复待实机确认 |
| **模块** | `area: daily` / `area: pvp` / `area: trade` / `area: collection` / `area: desktop` / `area: vision-ocr` / `area: build-ci` | 对应业务任务、桌面端、视觉 OCR 或构建 CI 领域 |
| **协作** | `good first issue` / `help wanted` / `duplicate` / `wontfix` | 适合新手、欢迎认领、重复单、暂不处理 |

## 8. 行为与许可

- 本项目采用 [GPL-3.0 许可证](LICENSE) 发布，提交贡献即表示你同意你的代码按同一许可证开源。
- 请在 Issue 与 PR 讨论中保持友善、理性与就事论事的技术沟通。
