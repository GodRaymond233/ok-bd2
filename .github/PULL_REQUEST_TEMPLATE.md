<!--
PR 标题请遵循格式：<type>(<scope>): <简明描述>（支持中/英文）
示例：
  fix(glutalk): 跳过全屏相遇并关闭满好感约会奖励页
  feat(trade): 新增跑商收益统计
详见 CONTRIBUTING.md。
-->

## 变更说明 / Summary

<!-- 简要说明本次 PR 解决了什么问题或新增了什么功能，如有对应 Issue 请使用 Closes #xxx 关联。 -->

## 变更类型 / Type of Change

- [ ] Bug 修复（`fix`）
- [ ] 新功能（`feat`）
- [ ] 重构或性能优化（`refactor` / `perf`）
- [ ] 文档、模板或配置更新（`docs` / `chore`）
- [ ] 测试、CI 或构建流程调整（`test` / `ci` / `build`）

## 验证方式 / Verification

<!-- 说明本地运行过的测试、检查命令、原图回放或实机验证情况（分辨率 / 游戏场景）。 -->

- [ ] Python 门禁：`.\scripts\run_checks.ps1 -Mode Final`
- [ ] 桌面端检查（如涉及 `desktop/`）：`npm run check` & `npm test`
- [ ] 实机或原图回放验证（如涉及识图、OCR、坐标或游戏交互）

## 提交前自查 / Checklist

- [ ] PR 标题符合 `<type>(<scope>): <简明描述>` 格式，且本 PR 聚焦单一改动主题
- [ ] 遵守 BD2 PC 端输入限制：`src/` 中未引入任何自定义键盘发送、按键映射或热键方案
- [ ] 固定界面点击点位使用相对比例坐标（0–1），未写死单一分辨率像素坐标
- [ ] 剧情卡带箱庭、跑商商人场景与 PVP 箱庭确认信号保持隔离，未在通用分类器中混用
- [ ] 若改动依赖，已通过 `.\scripts\refresh_dependencies.ps1` 同步锁定与导出，未手改 `requirements*.txt`
- [ ] 相关文档或测试已同步更新，无无关文件混入
