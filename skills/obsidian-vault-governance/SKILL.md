---
name: obsidian-vault-governance
description: 编辑、整理或审查中文 Obsidian 知识库时，应用随附的目录治理、Markdown 和知识写作规范。
---

## 1. 适用范围

用于 Obsidian 知识库，不将其中的目录和标题约定施加到无关代码仓库。事实来自目标笔记及相关材料；目标仓库的规则和用户要求优先。随附参考已完整包含本 Skill 的规范，使用时无需另建 `.standards/`。

## 2. 安装与验收

按 [installation.md](references/installation.md)将完整目录放入目标客户端的 Skills 位置。本 Skill 仅含 Markdown，无 Python、Node.js、飞书或 lark-cli 依赖，原生 Windows 也可按客户端支持的位置手动复制。

重新加载后让客户端读取一篇已有笔记并说明适用规则，确认能访问当前 Skill 内的三个参考；验收无需写入或搬迁笔记。更新替换完整目录，回滚恢复旧目录，卸载只移除 Skill，不删除知识库内容。

## 3. 按需读取

| 当前任务 | 参考 |
|---|---|
| 目录、文档职责或结构调整 | [repository-governance.md](references/repository-governance.md) |
| Markdown 标题、排版、链接、公式 | [style-guide.md](references/style-guide.md) |
| `knowledge-base/` 或知识解释、方法、机制、对比文档 | [knowledge-base-writing-guide.md](references/knowledge-base-writing-guide.md) |

先读目标笔记及必要的邻近材料，再按表选择参考；不默认加载全部文件。

## 4. 编辑与交付

保持目标目录语义、内容事实和术语。优先修改已有文档；搬迁、拆分或增加目录应解决具体职责问题。治理细则只维护一个来源，入口使用链接；若目标库确有多份独立维护的镜像规则，同步其对应内容。

完成后检查本次涉及的事实、层级、编号、相对链接和导航。知识文档的总览应能独立表达结论与适用边界，正文围绕该主线展开。
