"""Translate the MCP command allowlist to the official Colab CLI."""
from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass
import json
import math
import os
from pathlib import Path
import re
import selectors
import signal
import subprocess
import sys
import tempfile
import threading
import time

MAX_INPUT = 1024 * 1024
MAX_FILE = 4 * 1024 * 1024
MAX_OUTPUT = 256 * 1024
TIMEOUT = 240
_CALL_SLOT = threading.Lock()

_SESSION = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}")
_PACKAGE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.!+<>=~,*\[\]-]{0,255}")
_SECRET = re.compile(
    r"(?i)((?<![\w-])(?:[\w-]*token|client_secret)"
    r"(?:=|%3D|[\"']?\s*:\s*[\"']?)|\b(?:Bearer|Basic)\s+)[^\s&\"',)<>]+"
)
_S = {"-s": "--session", "--session": "--session"}
_HARDWARE = {"--gpu": "--gpu", "--tpu": "--tpu", "--high-mem": None}
_EXECUTION = {"--timeout": "--timeout"}
_LOG = {"-n": "--lines", "--lines": "--lines", "-t": "--type", "--type": "--type"}
_OPTIONS = {
    "version": {}, "usage": {}, "sessions": {},
    "new": {**_S, **_HARDWARE},
    "status": _S, "stop": _S, "restart-kernel": _S, "url": _S,
    "exec": {**_S, **_EXECUTION, "-f": "--file", "--file": "--file"},
    "run": {**_S, **_HARDWARE, **_EXECUTION, "--keep": None},
    "install": _S,
    "ls": _S, "rm": _S, "upload": _S, "download": _S,
    "log": {**_S, **_LOG, "-o": "--output", "--output": "--output"},
}


@dataclass
class Command:
    name: str
    options: dict[str, str | None]
    positionals: list[str]
    stdin: bytes | None
    file: bytes | None
    help: bool = False


def _parse_options(name: str, args: list[str]) -> tuple[dict[str, str | None], list[str]]:
    options: dict[str, str | None] = {}
    positionals: list[str] = []
    tokens = iter(args)
    for arg in tokens:
        if arg == "--":
            positionals.extend(tokens)
            break
        if not arg.startswith("-"):
            positionals.append(arg)
            continue
        flag, equals, inline_value = arg.partition("=")
        if flag not in _OPTIONS[name]:
            raise ValueError(f"unsupported option for {name}")
        canonical = _OPTIONS[name][flag] or flag
        if canonical in options:
            raise ValueError(f"duplicate option {canonical}")
        if _OPTIONS[name][flag] is None:
            if equals:
                raise ValueError(f"{canonical} takes no value")
            options[canonical] = None
            continue
        value = inline_value if equals else next(tokens, "")
        if not value or value.startswith("-"):
            raise ValueError(f"{canonical} requires a value")
        options[canonical] = value
    return options, positionals


def _remote_path(path: str, *, allow_root: bool = False) -> None:
    parts = path.strip("/").split("/")
    if path == "/" and allow_root:
        return
    if any(part in {"", ".", ".."} for part in parts) or "\\" in path:
        raise ValueError("use a Colab runtime path without empty, dot or parent components")


def _filename(value: str, suffixes: tuple[str, ...] = ()) -> None:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", value):
        raise ValueError("use a filename without directories for client files")
    if suffixes and Path(value).suffix not in suffixes:
        raise ValueError(f"supported filename extensions: {', '.join(suffixes)}")


def _validate_options(options: dict[str, str | None]) -> None:
    if "--session" in options and not _SESSION.fullmatch(options["--session"]):
        raise ValueError("session name must be 1–64 letters, digits, hyphens or underscores")
    if "--gpu" in options and "--tpu" in options:
        raise ValueError("choose either a GPU or a TPU")
    for flag in ("--gpu", "--tpu", "--type"):
        if flag in options and not _SESSION.fullmatch(options[flag]):
            raise ValueError(f"invalid {flag} value")
    if "--lines" in options and not re.fullmatch(r"[1-9][0-9]{0,3}", options["--lines"]):
        raise ValueError("--lines must be an integer from 1 to 9999")
    if "--timeout" in options:
        seconds = float(options["--timeout"])
        if not math.isfinite(seconds) or not 0 <= seconds <= TIMEOUT:
            raise ValueError(f"--timeout must be from 0 to {TIMEOUT} seconds")


def parse_command(args: list[str], stdin: str | None, file_base64: str | None) -> Command:
    if len(args) > 128 or any(
        len(arg.encode()) > 4096 or any(ord(c) < 32 or ord(c) == 127 for c in arg)
        for arg in args
    ):
        raise ValueError("use at most 128 arguments of 4096 bytes each, without control characters")
    if not args or args[0] not in _OPTIONS:
        raise ValueError(f"args must start with one of {', '.join(_OPTIONS)}")
    name, *tokens = args
    if tokens in (["-h"], ["--help"]) and stdin is None and file_base64 is None:
        return Command(name, {}, [], None, None, help=True)
    options, positions = _parse_options(name, tokens)
    _validate_options(options)

    if name in {"run", "install"}:
        if not positions:
            raise ValueError(f"{name} requires at least one positional argument")
    elif name in {"upload", "download"}:
        if len(positions) != 2:
            raise ValueError(f"{name} needs a source and destination")
    elif name == "rm":
        if len(positions) != 1:
            raise ValueError("rm needs one remote path")
    elif name == "ls":
        if len(positions) > 1:
            raise ValueError("ls takes at most one remote path")
    elif positions:
        raise ValueError(f"{name} takes no positional arguments")

    if name in {"ls", "rm", "upload", "download"} and positions:
        _remote_path(positions[-1] if name == "upload" else positions[0], allow_root=name == "ls")
    if name == "install" and any(not _PACKAGE.fullmatch(package) for package in positions):
        raise ValueError("install accepts package names and version specifiers")
    if name == "run":
        _filename(positions[0], (".py",))
        if any(re.match(r"--env(?:=|$)", value) for value in positions[1:]):
            raise ValueError("run arguments cannot set remote environment variables via --env")
    if name in {"upload", "download"}:
        _filename(positions[0] if name == "upload" else positions[1])
    if "--file" in options:
        _filename(options["--file"], (".py", ".ipynb"))
    if "--output" in options:
        if "--session" not in options:
            raise ValueError("log export requires --session")
        _filename(options["--output"], (".ipynb", ".md", ".txt", ".jsonl"))

    needs_file = name in {"upload", "run"} or "--file" in options
    if needs_file != (file_base64 is not None):
        raise ValueError("file_base64 is required only for upload, run and exec -f")
    needs_stdin = name == "exec" and not needs_file
    if needs_stdin != (stdin is not None):
        raise ValueError("stdin is required only for exec without -f")
    data = stdin.encode() if stdin is not None else None
    if data is not None and (not data.strip() or len(data) > MAX_INPUT):
        raise ValueError("stdin must contain Python code of at most 1 MiB")
    file = None
    if file_base64 is not None:
        if len(file_base64) > 4 * ((MAX_FILE + 2) // 3):
            raise ValueError("file exceeds 4 MiB")
        try:
            file = base64.b64decode(file_base64, validate=True)
        except (ValueError, binascii.Error) as error:
            raise ValueError("file_base64 must be valid base64") from error
        if len(file) > MAX_FILE:
            raise ValueError("file exceeds 4 MiB")
    return Command(name, options, positions, data, file)


def _prepare(command: Command, work: Path) -> tuple[list[str], str | None]:
    name = command.name
    if command.help:
        return [name, "--help"], None
    options = command.options.copy()
    positions = command.positionals.copy()
    input_name = output_name = None
    if name in {"upload", "run"}:
        input_name = positions[0]
        positions[0] = f"./{input_name}"
    if "--file" in options:
        input_name = options["--file"]
        options["--file"] = f"./{input_name}"
        if input_name.endswith(".ipynb"):
            output_name = f"{Path(input_name).stem}_output.ipynb"
    if name == "download":
        output_name = positions[1]
        positions[1] = f"./{output_name}"
    if "--output" in options:
        output_name = options["--output"]
        options["--output"] = f"./{output_name}"
    if input_name is not None:
        (work / input_name).write_bytes(command.file)
    argv = [name]
    for flag, value in options.items():
        argv.append(flag)
        if value is not None:
            argv.append(value)
    if positions:
        argv.extend(["--", *positions])
    return argv, output_name


def _state_home() -> Path:
    return Path(os.environ.get("COLAB_MCP_STATE_DIR", "/var/lib/colab-mcp"))


def _check_session_name(command: Command) -> None:
    # The upstream CLI assigns before saving, and saving replaces any entry
    # with the same name. Protect its record of the previous runtime.
    if command.help or command.name not in {"new", "run"}:
        return
    try:
        stored = (_state_home() / ".config/colab-cli/sessions.json").read_text()
        sessions = json.loads(stored) if stored.strip() else {}
    except FileNotFoundError:
        return
    except (OSError, ValueError) as error:
        raise RuntimeError("Colab session state could not be read") from error
    if not isinstance(sessions, dict):
        raise RuntimeError("Colab session state could not be read")
    if command.options.get("--session") in sessions:
        raise ValueError("session name already exists; use status, stop it or choose another name")


def _run(argv: list[str], stdin: bytes | None, work: Path) -> dict:
    """Drain both pipes, retaining bounded output and killing the process group on timeout."""
    entry = Path(__file__).resolve().with_name("colab_entry.py")
    env = {
        "PATH": os.defpath,
        "HOME": str(_state_home()),
        "GOOGLE_APPLICATION_CREDENTIALS": os.environ.get(
            "COLAB_MCP_ADC_FILE", "/run/colab/adc.json"
        ),
        "LANG": "C.UTF-8",
        "NO_COLOR": "1",
        "TERM": "dumb",
        "TMPDIR": str(work),
    }
    with tempfile.TemporaryFile(dir=work) as input_file:
        if stdin is not None:
            input_file.write(stdin)
            input_file.seek(0)
        process = subprocess.Popen(
            [sys.executable, "-B", str(entry), "--auth", "adc", *argv],
            stdin=input_file, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            cwd=work, env=env, start_new_session=True,
        )
        chunks = {"stdout": bytearray(), "stderr": bytearray()}
        original = dict.fromkeys(chunks, 0)
        timed_out = False
        deadline = time.monotonic() + TIMEOUT
        try:
            with selectors.DefaultSelector() as selector:
                selector.register(process.stdout, selectors.EVENT_READ, "stdout")
                selector.register(process.stderr, selectors.EVENT_READ, "stderr")
                while selector.get_map():
                    events = selector.select(max(0, deadline - time.monotonic()))
                    if time.monotonic() >= deadline:
                        raise subprocess.TimeoutExpired(argv, TIMEOUT)
                    for key, _ in events:
                        block = os.read(key.fd, 65536)
                        if not block:
                            selector.unregister(key.fileobj)
                            continue
                        original[key.data] += len(block)
                        stream = chunks[key.data]
                        stream.extend(block[:MAX_OUTPUT - len(stream)])
            process.wait(timeout=max(0, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            timed_out = True
        finally:
            if process.returncode is None:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait()
            process.stdout.close()
            process.stderr.close()
    result = {
        "exit_code": None if timed_out else process.returncode,
        "timed_out": timed_out,
        **{
            name: _SECRET.sub(r"\1[redacted]", data.decode(errors="replace"))
            for name, data in chunks.items()
        },
    }
    if any(size > MAX_OUTPUT for size in original.values()):
        result.update(truncated=True, original_bytes=original)
    return result


def call_cli(args: list[str], stdin: str | None = None, file_base64: str | None = None) -> dict:
    command = parse_command(args, stdin, file_base64)
    if not _CALL_SLOT.acquire(blocking=False):
        raise RuntimeError("another Colab command is running; this call did not start")
    try:
        _check_session_name(command)
        with tempfile.TemporaryDirectory(prefix="colab-mcp-") as temporary:
            work = Path(temporary)
            argv, output_name = _prepare(command, work)
            result = _run(argv, command.stdin, work)
            if output_name and result["exit_code"] == 0:
                artifact = work / output_name
                if not artifact.is_file():
                    result["file_error"] = "Colab did not produce an output file"
                elif artifact.stat().st_size > MAX_FILE:
                    result["file_error"] = "output file exceeds the 4 MiB MCP limit"
                else:
                    result["file"] = {
                        "name": output_name,
                        "base64": base64.b64encode(artifact.read_bytes()).decode("ascii"),
                    }
            return result
    finally:
        _CALL_SLOT.release()
