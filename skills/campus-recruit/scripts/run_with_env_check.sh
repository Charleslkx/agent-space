#!/usr/bin/env sh

set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PROJECT_DIR=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)
PROJECT_NAME=$(basename "$PROJECT_DIR")
REQUIRED_PYTHON_VERSION=3.11
REQUIRED_PYTHON_SPEC=">=3.11"
UV_CACHE_DIR=${UV_CACHE_DIR:-"$PROJECT_DIR/.uv-cache"}
DEFAULT_ENTRYPOINT="scripts/query_base.py"

log() {
    printf '%s\n' "$1"
}

fail() {
    printf 'Error: %s\n' "$1" >&2
    exit 1
}

has_cmd() {
    command -v "$1" >/dev/null 2>&1
}

run_uv() {
    mkdir -p "$UV_CACHE_DIR"
    (cd "$PROJECT_DIR" && env UV_CACHE_DIR="$UV_CACHE_DIR" uv "$@")
}

ensure_python() {
    if has_cmd python3; then
        PYTHON_BIN=python3
        return
    fi

    if has_cmd python; then
        PYTHON_BIN=python
        return
    fi

    fail "Python not found. Install Python first."
}

write_python_version_file() {
    if [ ! -f "$PROJECT_DIR/.python-version" ]; then
        log "Creating .python-version with Python $REQUIRED_PYTHON_VERSION"
        printf '%s\n' "$REQUIRED_PYTHON_VERSION" > "$PROJECT_DIR/.python-version"
    fi
}

write_default_pyproject() {
    if [ -f "$PROJECT_DIR/pyproject.toml" ]; then
        return
    fi

    log "Creating pyproject.toml for a new uv project"
    {
        printf '[project]\n'
        printf 'name = "%s"\n' "$PROJECT_NAME"
        printf 'version = "0.1.0"\n'
        printf 'description = "Project initialized by run_with_env_check.sh"\n'
        printf 'readme = "SKILL.md"\n'
        printf 'requires-python = "%s"\n' "$REQUIRED_PYTHON_SPEC"
        printf 'dependencies = []\n'
    } > "$PROJECT_DIR/pyproject.toml"
}

install_with_uv() {
    has_cmd uv || fail "uv environment detected, but uv is not installed."
    write_python_version_file
    write_default_pyproject

    if [ ! -f "$PROJECT_DIR/uv.lock" ]; then
        log "uv.lock not found. Initializing lockfile with: uv lock"
        run_uv lock
    fi

    log "Using uv to sync project environment"
    run_uv sync
}

install_with_poetry() {
    has_cmd poetry || fail "poetry environment detected, but poetry is not installed."
    log "Detected Poetry project. Running: poetry install"
    (cd "$PROJECT_DIR" && poetry install)
}

install_with_pipenv() {
    has_cmd pipenv || fail "Pipenv environment detected, but pipenv is not installed."
    log "Detected Pipenv project. Running: pipenv install"
    (cd "$PROJECT_DIR" && pipenv install)
}

install_with_conda() {
    has_cmd conda || fail "Conda environment detected, but conda is not installed."

    if [ -f "$PROJECT_DIR/environment.yml" ]; then
        log "Detected Conda project. Running: conda env update -f environment.yml --prune"
        (cd "$PROJECT_DIR" && conda env update -f environment.yml --prune)
        return
    fi

    if [ -f "$PROJECT_DIR/environment.yaml" ]; then
        log "Detected Conda project. Running: conda env update -f environment.yaml --prune"
        (cd "$PROJECT_DIR" && conda env update -f environment.yaml --prune)
        return
    fi

    fail "Conda environment detected, but no environment.yml or environment.yaml was found."
}

install_with_pip() {
    if [ -f "$PROJECT_DIR/requirements.txt" ]; then
        log "Using pip. Running: $PYTHON_BIN -m pip install -r requirements.txt"
        (cd "$PROJECT_DIR" && "$PYTHON_BIN" -m pip install -r requirements.txt)
        return
    fi

    if [ -f "$PROJECT_DIR/pyproject.toml" ]; then
        log "Using pip. Running: $PYTHON_BIN -m pip install -e ."
        (cd "$PROJECT_DIR" && "$PYTHON_BIN" -m pip install -e .)
        return
    fi

    fail "No dependency file found for pip."
}

detect_manager() {
    if [ -f "$PROJECT_DIR/uv.lock" ]; then
        printf 'uv\n'
        return
    fi

    if has_cmd uv; then
        printf 'uv\n'
        return
    fi

    if [ -f "$PROJECT_DIR/poetry.lock" ]; then
        printf 'poetry\n'
        return
    fi

    if [ -f "$PROJECT_DIR/Pipfile" ] || [ -f "$PROJECT_DIR/Pipfile.lock" ]; then
        printf 'pipenv\n'
        return
    fi

    if [ -f "$PROJECT_DIR/environment.yml" ] || [ -f "$PROJECT_DIR/environment.yaml" ]; then
        printf 'conda\n'
        return
    fi

    printf 'pip\n'
}

run_next_step() {
    if [ "$#" -gt 0 ]; then
        if [ "$MANAGER" = "uv" ]; then
            log "Environment check passed. Running with uv: uv run $*"
            run_uv run "$@"
            return
        fi

        log "Environment check passed. Running: $*"
        (cd "$PROJECT_DIR" && "$@")
        return
    fi

    if [ "$MANAGER" = "uv" ]; then
        log "Environment check passed. Running default step with uv: uv run $PYTHON_BIN $DEFAULT_ENTRYPOINT"
        run_uv run "$PYTHON_BIN" "$DEFAULT_ENTRYPOINT"
        return
    fi

    log "Environment check passed. Running default step: $PYTHON_BIN $DEFAULT_ENTRYPOINT"
    (cd "$PROJECT_DIR" && "$PYTHON_BIN" "$DEFAULT_ENTRYPOINT")
}

ensure_python
MANAGER=$(detect_manager)

case "$MANAGER" in
    uv)
        install_with_uv
        ;;
    poetry)
        install_with_poetry
        ;;
    pipenv)
        install_with_pipenv
        ;;
    conda)
        install_with_conda
        ;;
    pip)
        install_with_pip
        ;;
    *)
        fail "Unsupported environment manager: $MANAGER"
        ;;
esac

run_next_step "$@"
