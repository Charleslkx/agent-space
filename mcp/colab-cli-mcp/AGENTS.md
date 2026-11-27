# Google Colab CLI MCP — Agent guide

## 1. Connect and call the tool

Connect an OAuth-capable client to `https://colab.example.com/mcp` and complete GitHub login. Allowlisted GitHub users share the deployment's Google account, runtimes, history and compute quota. Use a distinct session name for each workflow and keep it across calls.

| Input | Type | When to send |
|---|---|---|
| `args` | Array of strings, required | Start with the subcommand. Omit the executable name. Use 1–128 tokens, each up to 4096 UTF-8 bytes and free of ASCII control characters. Pass each flag, value and operand as a separate element, or use `--flag=value`. |
| `stdin` | String or null | Required for `exec` without `-f`. Send nonblank Python source as UTF-8 text, up to 1 MiB. Omit or send null for other calls. |
| `file_base64` | String or null | Required for `upload`, `run` and `exec -f`. Encode the file bytes as standard base64 without whitespace or a data URL prefix. The decoded file can be up to 4 MiB. Omit or send null for other calls. |

Use `["COMMAND","--help"]` or `["COMMAND","-h"]` to check the installed CLI. The tables below list the flags accepted by this MCP. Put `--` before an operand starting with a dash. Use `["run","-s","job","main.py","--","--count","10"]` to forward `--count 10` to a script.

## 2. Commands and parameters

Brackets in the command forms mark optional tokens. Uppercase words denote values to replace.

### Runtimes

| Command form | Behavior |
|---|---|
| `new [-s NAME] [--gpu MODEL]` or `new [-s NAME] [--tpu MODEL]` with optional `--high-mem` | Allocates a runtime. Omit both accelerator flags for CPU. Omit the session name to let the CLI generate one. |
| `sessions` | Lists active runtimes on the shared account, including ones without a local name. |
| `status [-s NAME]` | Shows hardware, machine shape and kernel status. With no name it shows all tracked runtimes. |
| `restart-kernel [-s NAME]` | Restarts the selected Jupyter kernel and clears in-memory variables. |
| `stop [-s NAME]` | Releases the VM. Retrieve any needed files first. |
| `url [-s NAME]` | Returns the browser URL for the selected runtime. |

`-s` and `--session` are aliases. Names are case-sensitive, contain 1–64 ASCII letters, digits, hyphens or underscores, and start with a letter or digit. Commands that target one existing runtime can infer it when exactly one local session is active. `status` and `log` have the separate defaults shown in their rows. An explicit name already present in local state is rejected by `new` and `run`. Use `status` to refresh local session records or `stop` to release an active runtime before reusing its name.

The tested CLI 0.8.0 accepts GPU models T4, L4, G4, H100 and A100, and TPU models v5e1 and v6e1. These values are case-insensitive. Select at most one accelerator flag. Availability depends on the Google account, quota and capacity. `--high-mem` requests extra RAM where available. The CLI can ignore it for hardware with one memory shape. Verify the allocated shape with `status`. [Official Colab CLI 0.8.0](https://pypi.org/project/google-colab-cli/0.8.0/)

### Execution and dependencies

| Command form | Behavior |
|---|---|
| `exec [-s NAME] [--timeout SECONDS]` | Executes the `stdin` source in an existing Colab kernel. Variables can persist for later calls in that kernel. |
| `exec [-s NAME] [--timeout SECONDS] -f FILE` | Executes a supplied `.py` or `.ipynb` file. Use `file_base64`. A notebook with outputs can be returned as `STEM_output.ipynb`. |
| `run [-s NAME] [--gpu MODEL]` or `run [-s NAME] [--tpu MODEL]`, followed by optional `--high-mem`, `--keep`, `--timeout SECONDS`, then `SCRIPT.py [-- ARGS...]` | Runs one supplied script on a fresh runtime. Use `file_base64`. The CLI attempts to release the runtime when execution finishes. Use `--keep` and a known `-s NAME` to access its files and stop it later. |
| `install [-s NAME] PACKAGE [PACKAGE...]` | Installs packages into a running Colab VM. Use package names and optional extras or version constraints, such as `numpy`, `requests[socks]` or `pandas>=2`. |

`-f` and `--file` are aliases. `--timeout` accepts whole or fractional seconds from 0 to 240 and defaults to 30 seconds of inactivity. Zero disables the CLI's inactivity timeout. Every MCP call still has a 240-second total deadline. Package URLs, requirements files, pip flags and remote `--env` are outside this interface.

### Files, history and account

| Command form | Behavior |
|---|---|
| `ls [-s NAME] [REMOTE]` | Lists a path in the Colab runtime. The default path is `/content`. |
| `rm [-s NAME] REMOTE` | Requests deletion of the remote entry. |
| `upload [-s NAME] FILE REMOTE` | Sends `file_base64` to the destination on Colab. This can overwrite an existing file. |
| `download [-s NAME] REMOTE FILE` | Reads one remote file. Decode the returned `file.base64` and save it with `file.name` on the client. |
| `log [-s NAME] [-n N] [-t TYPE] [-o FILE]` | Lists session names with history when no name is supplied. With a name it shows a summary or exports the history. |
| `usage` | Shows the shared account's compute-unit usage rate and balance. |
| `version` | Shows the installed Google Colab CLI version. |

`FILE` and `SCRIPT.py` are plain filenames without directories. Use 1–128 ASCII letters, digits, dots, hyphens or underscores, starting with a letter or digit. `REMOTE` is a runtime path such as `/content/results.csv`. Pass the whole path in one token. Paths can be relative to the remote file API root or start with `/`. Omit empty interior components, `.`, `..` and backslashes. The root path `/` is accepted by `ls`.

For `log`, `-n` or `--lines` selects the last 1–9999 matching entries. With no limit, all matching entries are selected. `-t` or `--type` filters by event type such as `execution`, `file_operation` or `automation`. `-o` or `--output` exports to a filename ending in `.ipynb`, `.md`, `.txt` or `.jsonl` and requires `-s`. History can include submitted code, files and output. Only pass it to clients allowed to see that data.

## 3. Example arguments

A CPU workflow creates a named runtime, executes an expression and releases the runtime. Send each JSON object as a separate tool call.

```json
{"args":["new","-s","demo"]}
```

```json
{"args":["exec","-s","demo"],"stdin":"print(2 + 2)"}
```

```json
{"args":["stop","-s","demo"]}
```

These file examples assume the named session is active. `cHJpbnQoNCkK` is the base64 encoding of the Python file `print(4)` followed by a newline.

```json
{"args":["exec","-s","demo","-f","sample.py"],"file_base64":"cHJpbnQoNCkK"}
```

```json
{"args":["upload","-s","demo","sample.py","/content/sample.py"],"file_base64":"cHJpbnQoNCkK"}
```

```json
{"args":["download","-s","demo","/content/sample.py","sample.py"]}
```

```json
{"args":["log","-s","demo","--type","execution","--lines","10","--output","history.ipynb"]}
```

For a standalone script job, use a new name. A kept runtime can be used by later tool calls.

```json
{"args":["run","-s","job","--keep","sample.py","--","--count","10"],"file_base64":"cHJpbnQoNCkK"}
```

## 4. Read the result

Use the MCP result's `structuredContent` when available. Clients may also display the result as JSON text.

| Field | Meaning |
|---|---|
| `exit_code` | Local CLI exit code, or null on an MCP timeout. Zero indicates CLI completion. Read both output streams for kernel exceptions or messages such as a missing session. |
| `stdout`, `stderr` | UTF-8 decoded strings. The wrapper masks recognized credential patterns. |
| `timed_out` | The MCP deadline expired. Colab may still be running the code or allocating a VM. Inspect `sessions` and `status` before retrying. |
| `truncated`, `original_bytes` | Present when either stream exceeds 256 KiB. `original_bytes.stdout` and `original_bytes.stderr` count bytes observed before truncation. |
| `file.name`, `file.base64` | Returned artifact name and standard base64 data. Available for successful file-producing calls that generate an artifact up to 4 MiB. |
| `file_error` | The CLI exited successfully but the expected artifact was missing or too large. Check this even when `exit_code` is zero. |

Validation failures and busy responses are MCP tool errors. A busy response means the CLI command never started. Check the requested command and required inputs before retrying a validation failure. Authentication, config overrides, browser launches, interactive terminals, SSH, local editors and CLI updates are managed outside the tool. [Deployment and configuration](CONFIGURATION.md)
