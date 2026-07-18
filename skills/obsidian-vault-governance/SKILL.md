---
name: obsidian-vault-governance
description: Apply the bundled repository governance, Markdown, and knowledge-base writing rules when editing, reorganizing, reviewing, or adding content in a Chinese-first Obsidian knowledge vault. Use whenever the task concerns vault notes, repository policy, Markdown links, document structure, or knowledge-base documentation, even if the user names only a note or subdirectory.
compatibility: Self-contained. Requires only filesystem access to the target vault.
---

## 1 Purpose and scope

Use this skill for a Chinese-first Obsidian knowledge vault whose content is mainly Markdown. All repository rules it needs are bundled in this skill; do not require an external `.standards/` directory to apply them.

Keep two facts separate:

- Content facts come from the target note and nearby notes.
- Structure, format, and change boundaries come from the repository governance documents.

Do not invent facts, workflows, directory responsibilities, or theme-specific rules that are not supported by the vault.

## 2 Bundled references

Read the relevant bundled reference before editing. The files are part of this skill and are therefore portable with it.

1. Read [repository-governance.md](references/repository-governance.md) for placement, document layers, directory semantics, or structural changes.
2. Read [style-guide.md](references/style-guide.md) for any Markdown edit, formatting, heading, table, math, or link work.
3. Read [knowledge-base-writing-guide.md](references/knowledge-base-writing-guide.md) when the target is in `knowledge-base/` or is a knowledge explanation, method summary, mechanism analysis, or comparison note.
4. Read the target note, then related notes in the same directory, for content facts and local style.

If the target vault has mirrored root policy files, update both for a repository-wide policy change. Check that any entry-document navigation still points to the changed governance material.

## 3 Document and directory decisions

Classify the requested change before writing:

- **Governance document:** explains how the vault is organized or edited. Put new governance rules in `.standards/`, keep them abstract, and do not encode current topics, note names, business facts, or temporary directory examples.
- **Entry document:** navigates readers or points to detailed rules. It may summarize scope but must not duplicate full governance details.
- **Content document:** explains a subject, project, question, or source material. Resolve facts from the target and its sibling notes.

Respect directory contracts:

- The root contains repository entry documents and a small number of global settings, not long-term subject content.
- `.standards/` contains repository-wide governance only.
- Other directories contain content and may evolve with their subjects.

Prefer editing an existing document. Put new content in the nearest existing topic directory. Add a directory only when existing semantics cannot hold the new content. Do not rename, move, split, delete, or broadly restructure notes merely to make the tree look tidier.

For a structural change, first establish that it resolves a real responsibility conflict. Avoid changing content facts, directory semantics, and governance rules in one edit unless they directly conflict.

## 4 Editing rules

Apply these rules to every Markdown edit:

- Do not use a level-one heading. Start top-level content at `## 1 标题`; use `### 1.1 标题` and `#### 1.1.1 标题` below it.
- Keep heading numbers continuous and consistent with their nesting level.
- Preserve necessary spaces between Chinese, English, numbers, and symbols.
- Preserve the note's existing terminology, voice, heading hierarchy, numbering, list indentation, table alignment, and emphasis conventions unless the task is a deliberate standardization.
- Use standard Markdown and common Obsidian syntax. Do not use HTML for layout.
- Use fenced code blocks and label their language where practical. Use `$...$` for inline math and `$$...$$` for displayed math.
- Do not add emoji, promotional wording, empty modifiers, made-up terminology, or English glosses after Chinese text.
- Put overview or comparison tables near the beginning when they materially improve reading.
- Complete only the requested work. Do not append unsolicited analysis, summaries, or evaluations.

## 5 Link rules

Use standard Markdown links for portable rendering across Obsidian, Typora, and other readers.

- Link repository files with relative paths: `[文字](../path/to/file.md)`.
- Encode spaces in link paths as `%20`.
- Link a heading in the current note with the complete numbered title and encoded spaces: `[文字](#2.1%20标题文本)`.
- Link a heading in another note as `[文字](file.md#2.1%20标题文本)`.
- Do not use Wiki links or custom HTML anchors as the general solution for heading jumps.

## 6 Knowledge-base documents

For `knowledge-base/` notes, require `## 1 总览` unless the existing document type clearly cannot support it.

The overview must:

- Start with one or two sentences that state the object and the central conclusion. Do not open with “本文介绍”.
- Give high-information content: the main conclusion, key relations, conditions, boundaries, or risks that matter for the topic.
- Let a reader understand the core claim and its applicability without reading the rest of the note.

Structure the body as “summary first, detail second.” Expand the problem defined in the overview; do not switch problem definitions halfway through. For comparisons, present dimensions before item-by-item detail. For mechanisms, state the causal chain or workflow early. Keep formulas, tables, and examples only when they help explain the current question.

Do not add a closing summary that merely repeats the overview or each section. Retain one only if it introduces a new decision rule, combined conclusion, or boundary.

## 7 Agent coordination

When delegating work, choose the lowest-capability model that can safely perform the task:

- Use a lightweight model for large-scale reading, extraction, deduplication, organization, or summarization that requires no new judgment.
- Use a stronger model for analysis, calculations, reasoning, code changes, fact verification, cross-document consistency, or risk decisions.
- Give each subagent a bounded scope, required output format, verification method, and a no-fabrication constraint.
- Resolve conflicting subagent conclusions in the primary agent before delivery.

## 8 Delivery checks

Before completing a task, check the smallest relevant set:

- The changed documents remain in the right responsibility layer and their directories retain their semantics.
- Markdown headings, numbering, spacing, lists, tables, and links conform to the rules above.
- The target's facts and local style are preserved.
- For `knowledge-base/` notes, the overview provides substantive content and the heading hierarchy is navigable.
- For policy changes, mirrored root policy files, relevant governance material, and entry-document navigation agree.
