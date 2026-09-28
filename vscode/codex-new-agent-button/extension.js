const vscode = require("vscode");
const { execFile } = require("node:child_process");
const { readdirSync } = require("node:fs");
const { homedir } = require("node:os");
const path = require("node:path");

const LEGACY_SHARED_DAEMON_SETTING = "useSharedDaemon";
const LEGACY_PREVIOUS_CLI_EXECUTABLE_KEY = "previousChatgptCliExecutable";

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

const PYTHON_LOCKS = `
import fcntl, json, os, sys
from pathlib import Path

root = Path(sys.argv[1])
action = sys.argv[2]
target = sys.argv[3] if len(sys.argv) > 3 else ""

def inspect(path):
    fd = os.open(path, os.O_RDONLY)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(fd, fcntl.LOCK_UN)
        return "stale"
    except BlockingIOError:
        return "live"
    finally:
        os.close(fd)

def release(path):
    fd = os.open(path, os.O_RDWR)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        os.unlink(path)
        return "released"
    except BlockingIOError:
        return "live"
    except FileNotFoundError:
        return "missing"
    except PermissionError:
        return "live"
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        except OSError:
            pass
        os.close(fd)

results = []
paths = []
if target:
    path = root / f"{target}.lock"
    if path.is_file():
        paths.append(path)
elif root.is_dir():
    paths.extend(sorted(p for p in root.glob("*.lock") if not p.name.startswith(".")))

for path in paths:
    session_id = path.stem
    try:
        if action == "release":
            status = release(path)
        else:
            status = inspect(path)
    except FileNotFoundError:
        status = "missing"
    except OSError:
        status = "unknown"
    results.append({"id": session_id, "status": status})

print(json.dumps(results))
`;

const exitingSessions = new Set();

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

function getConfig() {
  const configuration = vscode.workspace.getConfiguration("codexNewAgentButton");
  return {
    exitOnEditorClose: configuration.get("exitOnEditorClose", true),
    releaseStaleWriterLocks: configuration.get("releaseStaleWriterLocks", true),
  };
}

async function promptReload(message) {
  const choice = await vscode.window.showInformationMessage(
    message,
    "重新加载窗口",
  );
  if (choice === "重新加载窗口") {
    await vscode.commands.executeCommand("workbench.action.reloadWindow");
  }
}

function isLegacySharedDaemonExecutable(value) {
  if (typeof value !== "string") return false;
  const normalized = value.replaceAll("\\", "/");
  const isPluginPath =
    normalized.includes("/local.codex-new-agent-button-") ||
    normalized.includes("/vscode/codex-new-agent-button/");
  const isLegacyWrapper =
    normalized.endsWith("/bin/codex-shared-app-server") ||
    normalized.endsWith("/bin/codex-shared-app-server.cmd");
  return isPluginPath && isLegacyWrapper;
}

async function migrateLegacySharedDaemonConfig(context) {
  const chatgptConfig = vscode.workspace.getConfiguration("chatgpt");
  const current = chatgptConfig.get("cliExecutable", null);
  const previous = context.globalState.get(
    LEGACY_PREVIOUS_CLI_EXECUTABLE_KEY,
  );
  let restored = false;

  if (isLegacySharedDaemonExecutable(current)) {
    const previousValue =
      previous?.recorded &&
      !isLegacySharedDaemonExecutable(previous.value)
        ? previous.value
        : undefined;
    await chatgptConfig.update(
      "cliExecutable",
      previousValue,
      vscode.ConfigurationTarget.Global,
    );
    restored = true;
  }

  await context.globalState.update(
    LEGACY_PREVIOUS_CLI_EXECUTABLE_KEY,
    undefined,
  );

  const legacyConfig = vscode.workspace.getConfiguration(
    "codexNewAgentButton",
  );
  if (
    legacyConfig.inspect(LEGACY_SHARED_DAEMON_SETTING)?.globalValue !==
    undefined
  ) {
    await legacyConfig.update(
      LEGACY_SHARED_DAEMON_SETTING,
      undefined,
      vscode.ConfigurationTarget.Global,
    );
  }
  return restored;
}

function codexHome() {
  return process.env.CODEX_HOME || path.join(homedir(), ".codex");
}

function writerLockDir() {
  return path.join(codexHome(), "thread-writer-locks");
}

function pythonCommands() {
  return uniqueCommands([
    process.env.PYTHON,
    "python3",
    "python",
    "/usr/bin/python3",
    "/usr/local/bin/python3",
  ]);
}

async function runPython(args) {
  let lastMissing;
  for (const command of pythonCommands()) {
    try {
      return await runFile(command, args);
    } catch (error) {
      if (!isMissingCommand(error)) throw error;
      lastMissing = error;
    }
  }
  throw lastMissing || new Error("spawn python3 ENOENT");
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
  try {
    const { stdout } = await runPython([
      "-c",
      PYTHON_READER,
      database,
      SESSION_QUERY,
    ]);
    return JSON.parse(stdout || "[]");
  } catch (error) {
    if (isMissingCommand(error)) throw error;
    throw error;
  }
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

function latestSessionDatabase() {
  const home = codexHome();
  const database = readdirSync(home)
    .filter((name) => /^state_\d+\.sqlite$/.test(name))
    .sort((a, b) => Number(b.match(/\d+/)[0]) - Number(a.match(/\d+/)[0]))[0];
  return database ? path.join(home, database) : null;
}

function sessionIdFromUri(uri) {
  if (!uri || uri.scheme !== "openai-codex") return null;
  const match = String(uri.path || "").match(/\/local\/([^/]+)$/);
  return match ? match[1] : null;
}

function tabUri(tab) {
  return tab?.input?.uri ?? null;
}

function openCodexSessionIds() {
  const ids = new Set();
  for (const group of vscode.window.tabGroups.all) {
    for (const tab of group.tabs) {
      const id = sessionIdFromUri(tabUri(tab));
      if (id) ids.add(id);
    }
  }
  return ids;
}

function activeCodexSessionId() {
  const activeTab = vscode.window.tabGroups.activeTabGroup?.activeTab;
  return (
    sessionIdFromUri(tabUri(activeTab)) ||
    sessionIdFromUri(vscode.window.activeTextEditor?.document?.uri)
  );
}

async function inspectWriterLocks(sessionId) {
  try {
    const { stdout } = await runPython([
      "-c",
      PYTHON_LOCKS,
      writerLockDir(),
      "inspect",
      sessionId || "",
    ]);
    return JSON.parse(stdout || "[]");
  } catch (error) {
    if (isMissingCommand(error)) return [];
    return [];
  }
}

async function releaseStaleWriterLocks(sessionId) {
  try {
    const { stdout } = await runPython([
      "-c",
      PYTHON_LOCKS,
      writerLockDir(),
      "release",
      sessionId || "",
    ]);
    return JSON.parse(stdout || "[]");
  } catch (error) {
    if (isMissingCommand(error)) return [];
    throw error;
  }
}

function lockStatusLabel(status) {
  if (status === "live") return "占用中";
  if (status === "stale") return "僵尸锁";
  if (status === "released") return "已释放";
  return "空闲";
}

async function closeCodexEditors(sessionId) {
  const tabs = [];
  for (const group of vscode.window.tabGroups.all) {
    for (const tab of group.tabs) {
      const id = sessionIdFromUri(tabUri(tab));
      if (id && (!sessionId || id === sessionId)) tabs.push(tab);
    }
  }
  if (tabs.length > 0) await vscode.window.tabGroups.close(tabs, true);
}

async function exitSession(sessionId, { closeEditors = true } = {}) {
  if (!sessionId || exitingSessions.has(sessionId)) {
    return { sessionId, locks: [] };
  }

  exitingSessions.add(sessionId);
  try {
    if (closeEditors) await closeCodexEditors(sessionId);
    const locks = getConfig().releaseStaleWriterLocks
      ? await releaseStaleWriterLocks(sessionId)
      : [];
    return { sessionId, locks };
  } finally {
    exitingSessions.delete(sessionId);
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

async function openSession(sessionId, label) {
  const uri = vscode.Uri.parse(`openai-codex://route/local/${sessionId}`);
  if (label) await setSessionTabLabel(uri, label);
  await vscode.commands.executeCommand(
    "vscode.openWith",
    uri,
    "chatgpt.conversationEditor",
    { preview: false, viewColumn: vscode.ViewColumn.Active },
  );
}

async function pickSession(placeHolder) {
  const database = latestSessionDatabase();
  if (!database) throw new Error("未找到 Codex 会话数据库");

  const [sessions, locks] = await Promise.all([
    querySessions(database),
    inspectWriterLocks(),
  ]);
  const lockById = new Map(locks.map((lock) => [lock.id, lock.status]));
  const openIds = openCodexSessionIds();

  return vscode.window.showQuickPick(
    sessions.map((session) => {
      const lock = lockById.get(session.id);
      const marks = [
        openIds.has(session.id) ? "已打开" : null,
        lock ? lockStatusLabel(lock) : null,
      ].filter(Boolean);
      return {
        label: String(session.label).replace(/\s+/g, " ").slice(0, 120),
        description: session.cwd,
        detail: [new Date(session.recency_at_ms).toLocaleString(), ...marks]
          .filter(Boolean)
          .join(" · "),
        sessionId: session.id,
        lockStatus: lock || "none",
        sessionLabel: session.label,
      };
    }),
    {
      matchOnDescription: true,
      matchOnDetail: true,
      placeHolder,
    },
  );
}

async function resumeSession() {
  try {
    const selected = await pickSession("选择要在新标签页恢复的 Codex 会话");
    if (!selected) return;

    if (
      selected.lockStatus === "stale" &&
      getConfig().releaseStaleWriterLocks
    ) {
      await releaseStaleWriterLocks(selected.sessionId);
    } else if (selected.lockStatus === "live") {
      const choice = await vscode.window.showWarningMessage(
        "该 Codex 会话仍持有 writer 锁。先按标准路径退出，或仍尝试打开。",
        "先退出再打开",
        "仍要打开",
      );
      if (!choice) return;
      if (choice === "先退出再打开") {
        await exitSession(selected.sessionId);
      }
    }

    await openSession(selected.sessionId, selected.sessionLabel);
  } catch (error) {
    vscode.window.showErrorMessage(`无法恢复 Codex 会话：${error.message}`);
  }
}

async function exitCurrentOrPickedSession() {
  try {
    const sessionId = activeCodexSessionId();
    const selected = sessionId
      ? { sessionId }
      : await pickSession("选择要退出的 Codex 会话");
    if (!selected) return;

    const result = await exitSession(selected.sessionId);
    const lock = result.locks[0];
    if (lock?.status === "live") {
      vscode.window.showWarningMessage(
        "已关闭会话标签，但 writer 锁仍被进程占用。请确认对应 Codex 标签已关，或等待官方卸载完成。",
      );
      return;
    }
    vscode.window.showInformationMessage(
      lock?.status === "released"
        ? "已退出 Codex 会话并释放僵尸锁"
        : "已退出 Codex 会话",
    );
  } catch (error) {
    vscode.window.showErrorMessage(`无法退出 Codex 会话：${error.message}`);
  }
}

async function releaseAllStaleLocks() {
  try {
    const results = await releaseStaleWriterLocks();
    const released = results.filter((item) => item.status === "released");
    const live = results.filter((item) => item.status === "live");
    if (released.length === 0 && live.length === 0) {
      vscode.window.showInformationMessage("没有可清理的 Codex writer 锁");
      return;
    }
    vscode.window.showInformationMessage(
      `已释放 ${released.length} 个僵尸锁` +
        (live.length ? `，另有 ${live.length} 个仍被占用` : ""),
    );
  } catch (error) {
    vscode.window.showErrorMessage(`无法清理 Codex 会话锁：${error.message}`);
  }
}

function onTabsClosed(event) {
  if (!getConfig().exitOnEditorClose) return;

  const closedIds = new Set();
  for (const tab of event.closed) {
    const id = sessionIdFromUri(tabUri(tab));
    if (id) closedIds.add(id);
  }

  for (const sessionId of closedIds) {
    if (openCodexSessionIds().has(sessionId)) continue;
    exitSession(sessionId, { closeEditors: false }).catch((error) => {
      vscode.window.showErrorMessage(`无法退出 Codex 会话：${error.message}`);
    });
  }
}

async function activate(context) {
  context.subscriptions.push(
    vscode.commands.registerCommand("codexNewAgentButton.new", () =>
      vscode.commands.executeCommand("chatgpt.newCodexPanel"),
    ),
    vscode.commands.registerCommand(
      "codexNewAgentButton.history",
      resumeSession,
    ),
    vscode.commands.registerCommand(
      "codexNewAgentButton.exit",
      exitCurrentOrPickedSession,
    ),
    vscode.commands.registerCommand(
      "codexNewAgentButton.releaseStaleLocks",
      releaseAllStaleLocks,
    ),
    vscode.window.tabGroups.onDidChangeTabs(onTabsClosed),
  );

  try {
    if (await migrateLegacySharedDaemonConfig(context)) {
      await promptReload(
        "已移除旧的共享 Codex daemon 配置；重新加载窗口后将恢复使用官方 App Server。",
      );
    }
  } catch (error) {
    vscode.window.showErrorMessage(
      `无法清理旧的共享 Codex daemon 配置：${error.message}`,
    );
  }
}

module.exports = {
  activate,
  exitSession,
  inspectWriterLocks,
  isLegacySharedDaemonExecutable,
  migrateLegacySharedDaemonConfig,
  querySessions,
  releaseStaleWriterLocks,
  resumeSession,
};
