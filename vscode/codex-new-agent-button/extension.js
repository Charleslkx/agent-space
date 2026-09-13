const vscode = require("vscode");
const { execFile } = require("node:child_process");
const { readdirSync } = require("node:fs");
const { homedir } = require("node:os");
const path = require("node:path");

const SESSION_QUERY = `
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

const PYTHON_READER = `
import json, sqlite3, sys
from pathlib import Path

database = Path(sys.argv[1])
query = sys.argv[2]
uri = database.resolve().as_uri() + "?mode=ro"
try:
    con = sqlite3.connect(uri, uri=True)
except sqlite3.Error:
    con = sqlite3.connect(str(database))
con.row_factory = sqlite3.Row
print(json.dumps([dict(row) for row in con.execute(query)]))
`;

function runFile(command, args) {
  return new Promise((resolve, reject) => {
    execFile(
      command,
      args,
      { encoding: "utf8", windowsHide: true },
      (error, stdout, stderr) => {
        if (error) reject(error);
        else resolve({ stdout, stderr });
      },
    );
  });
}

function isMissingCommand(error) {
  return Boolean(error && error.code === "ENOENT");
}

function uniqueCommands(commands) {
  return [...new Set(commands.filter(Boolean))];
}

async function queryWithSqlite3(database) {
  const commands = uniqueCommands([
    process.env.SQLITE3,
    "sqlite3",
    "/usr/bin/sqlite3",
    "/usr/local/bin/sqlite3",
    "/opt/homebrew/bin/sqlite3",
  ]);
  let lastMissing;

  for (const command of commands) {
    try {
      const { stdout } = await runFile(command, [
        "-readonly",
        "-json",
        database,
        SESSION_QUERY,
      ]);
      return JSON.parse(stdout || "[]");
    } catch (error) {
      if (!isMissingCommand(error)) throw error;
      lastMissing = error;
    }
  }

  throw lastMissing || new Error("spawn sqlite3 ENOENT");
}

async function queryWithPython(database) {
  const commands = uniqueCommands([
    process.env.PYTHON,
    "python3",
    "python",
    "/usr/bin/python3",
    "/usr/local/bin/python3",
  ]);
  let lastMissing;

  for (const command of commands) {
    try {
      const { stdout } = await runFile(command, [
        "-c",
        PYTHON_READER,
        database,
        SESSION_QUERY,
      ]);
      return JSON.parse(stdout || "[]");
    } catch (error) {
      if (!isMissingCommand(error)) throw error;
      lastMissing = error;
    }
  }

  throw lastMissing || new Error("spawn python3 ENOENT");
}

async function querySessions(database) {
  try {
    return await queryWithSqlite3(database);
  } catch (error) {
    if (!isMissingCommand(error)) throw error;
  }

  try {
    return await queryWithPython(database);
  } catch (error) {
    if (isMissingCommand(error)) {
      throw new Error("未找到 sqlite3 或 python3，无法读取 Codex 会话");
    }
    throw error;
  }
}

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

    const sessions = await querySessions(path.join(codexHome, database));
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

module.exports = { activate, querySessions, resumeSession };
