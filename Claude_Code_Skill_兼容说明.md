# Claude Code Skill 兼容说明

## 支持的目录结构

现在 LightMe 会同时扫描两类技能：

```text
app/agent/skills/*.md
.claude/skills/<skill-name>/SKILL.md
```

第一类是项目原生格式，第二类是 Claude Code 常见技能格式。

## Claude Code 格式

示例：

```md
---
name: pdf
description: Use this skill whenever the user wants to do anything with PDF files.
license: Proprietary
---

# PDF Processing Guide

这里写技能说明、流程、注意事项和示例。
```

当前支持的 frontmatter 字段：

- `name`：技能名，会注册到 Agent 技能列表。
- `description`：技能描述，会参与 planner 和 skill matcher 判断。
- `category`：可选，默认 `general`。
- `keywords` 或 `trigger`：可选，作为关键词匹配补充。
- `tool_module` 或 `toolModule`：可选，对接 LightMe 原有技能工具模块机制。
- `license`：可选，会记录到 notes。

## 兼容行为

- `SKILL.md` 的正文会作为技能 instructions 注入 executor。
- `.claude/skills/<name>/SKILL.md` 会被递归发现。
- 原有 `# Skill: xxx` 格式不受影响。
- 如果技能重名，后加载的技能会覆盖先加载的技能，并打印警告。

## 当前限制

Claude Code 技能中的 `reference.md`、`forms.md`、`scripts/` 等附属文件会保留在原目录，但 Agent 目前不会自动展开读取这些附属文件。技能正文中如果写了“请读取 reference.md”，Agent 仍需要通过文件读取工具显式读取。

如果后续要做到更完整的 Claude Code 行为，可以继续增强：

1. 在 `SkillDef` 中暴露 `source_dir` 给 executor prompt。
2. 当技能正文引用相对文件时，自动将相关文件摘要注入上下文。
3. 将 `scripts/` 下的 Python 脚本包装成可调用工具。
