const vscode = require("vscode");
const { execFile } = require("node:child_process");
const { readdirSync } = require("node:fs");
const { homedir } = require("node:os");
const path = require("node:path");
const { promisify } = require("node:util");

const run = promisify(execFile);

async function setSessionTabLabel(uri, label) {
  const key = "workbench.editor.customLabels.patterns";
  const configuration = vscode.workspace.getConfiguration();
  const patterns = configuration.get(key, {});
  const title = /[a-z0-9]/i.test(label) ? label : `${label} · Codex`;
  await configuration.update(
    key,
    { ...patterns, [uri.path]: title },
    vscode.ConfigurationTarget.Global,
  );
}

async function resumeSession() {
  try {
    const codexHome = process.env.CODEX_HOME || path.join(homedir(), ".codex");
    const database = readdirSync(codexHome)
      .filter((name) => /^state_\d+\.sqlite$/.test(name))
      .sort((a, b) => Number(b.match(/\d+/)[0]) - Number(a.match(/\d+/)[0]))[0];

    if (!database) throw new Error("未找到 Codex 会话数据库");

    const query = `
      SELECT id,
             COALESCE(NULLIF(name, ''), NULLIF(preview, ''), title) AS label,
             cwd,
             recency_at_ms
      FROM threads
      WHERE archived = 0
        AND preview <> ''
        AND source IN ('vscode', 'cli', 'exec')
      ORDER BY recency_at_ms DESC
      LIMIT 100`;
    const { stdout } = await run("sqlite3", [
      "-readonly",
      "-json",
      path.join(codexHome, database),
      query,
    ]);
    const sessions = JSON.parse(stdout || "[]");
    const selected = await vscode.window.showQuickPick(
      sessions.map((session) => ({
        label: String(session.label).replace(/\s+/g, " ").slice(0, 120),
        description: session.cwd,
        detail: new Date(session.recency_at_ms).toLocaleString(),
        sessionId: session.id,
      })),
      {
        matchOnDescription: true,
        matchOnDetail: true,
        placeHolder: "选择要在新标签页恢复的 Codex 会话",
      },
    );

    if (!selected) return;

    const uri = vscode.Uri.parse(
      `openai-codex://route/local/${selected.sessionId}`,
    );
    await setSessionTabLabel(uri, selected.label);
    await vscode.commands.executeCommand(
      "vscode.openWith",
      uri,
      "chatgpt.conversationEditor",
      { preview: false, viewColumn: vscode.ViewColumn.Active },
    );
  } catch (error) {
    vscode.window.showErrorMessage(`无法恢复 Codex 会话：${error.message}`);
  }
}

function activate(context) {
  context.subscriptions.push(
    vscode.commands.registerCommand("codexNewAgentButton.new", () =>
      vscode.commands.executeCommand("chatgpt.newCodexPanel"),
    ),
    vscode.commands.registerCommand(
      "codexNewAgentButton.history",
      resumeSession,
    ),
  );
}

module.exports = { activate, resumeSession };
