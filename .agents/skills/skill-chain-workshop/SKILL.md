---
name: skill-chain-workshop
description: 当用户明确要求使用 Skill Chain Workshop 处理功能设计、Bug 修复、测试选择或质量验证时使用。
---

# Skill Chain Workshop

从本文件所在目录解析 `../../../.skill-chain-workshop/skills/`，它是本项目内的技能库。
按当前任务只读取相关文件：

- 修复 Bug：`fix-bug-workflow/SKILL.md`
- 设计新功能：`feat-design-planning/SKILL.md`
- 执行已确定的设计：`deep-planning-rigorous-validation/SKILL.md`
- 建立或查询项目知识：`knowledge-base-builder/SKILL.md`、`query-knowledge/SKILL.md`
- 敏感信息检查：`sensitive-info-filter/SKILL.md`
- 选择相关测试：`impact-driven-test/SKILL.md`、`test-pyramid-builder/SKILL.md` 及其 `scripts/select_tests.py`
- 多任务交付：`team-rigorous-delivery/SKILL.md`
- 业务质量验证：`ai-quality-engine/SKILL.md`

脚本使用技能库内的绝对路径，`--repo`、`--repo-root` 或 `--project-root` 指向当前业务项目。
原始技能如要求 `Skill`、`TaskCreate`、`superpowers`、`codegraph` 等外部工具，
先检查当前宿主是否具备；缺失时说明不能执行的步骤与门禁，不得声称已经通过。
