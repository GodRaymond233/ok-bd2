# <任务名> Handoff

> 用法：复制本模板为 `<任务名-小写连字符>.md`，一个文件对应一个持续推进的任务域（不按会话建文件）。
> 只写"下一位接手者需要知道什么"：已确认事实、已否决方案、当前真实状态。不写会话流水账，不复制 Git 可直接查询的历史，不贴大段代码。任务彻底结束后归档或删除本文件。
> 接手时先读本文件，再以当前源码与 Git 为准校验；两者冲突时以源码与 Git 为准，先报告差异。

## Goal

这项任务最终要解决什么。

## Current State

实现到了什么程度。区分：已完成 / 已实现未验证 / 部分完成 / 未解决 / 已否决（"写了代码"≠"问题解决"）。

## Confirmed Findings

经源码检查、测试或实机验证确认的事实，每条注明验证方式。

## Current Implementation

当前真正采用的方案与关键代码位置（文件/函数级指针即可）。

## Rejected / Failed Approaches

每项写清：尝试了什么 / 为什么失败 / 有什么证据 / 为什么不应重走这条路。

## Remaining Problems

仍未解决的问题。

## Relevant Files

最重要的源码、测试、probe、脚本、文档位置。

## Verification

已使用并确认有效的测试命令、启动命令、probe、benchmark、手工验证方法。

## Git State

branch、关键 commit、最后已验证版本。只记 Git 查询不到的上下文，不机械复制历史。

## Next Steps

按优先级列出下一步。

## Warnings / Constraints

继续开发时最容易踩的坑与不能破坏的约束。
