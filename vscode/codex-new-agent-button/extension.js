const vscode = require("vscode");

function activate(context) {
  context.subscriptions.push(
    vscode.commands.registerCommand("codexNewAgentButton.new", () =>
      vscode.commands.executeCommand("chatgpt.newCodexPanel"),
    ),
  );
}

module.exports = { activate };
