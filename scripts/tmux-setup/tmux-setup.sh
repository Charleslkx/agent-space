#!/usr/bin/env bash
# Install and maintain the repository's portable tmux configuration.
#
# The script deliberately tracks plugin default branches instead of pinning
# versions. A fresh run therefore installs the latest upstream stable branch,
# while an existing clean checkout is fast-forwarded when possible.

set -Eeuo pipefail
IFS=$'\n\t'

readonly SCRIPT_NAME="tmux-setup"
readonly SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
readonly TEMPLATE_PATH="$SCRIPT_DIR/tmux.conf"

user_home="${HOME:-}"
if [ -z "$user_home" ]; then
  user_home="$(getent passwd "$(id -u)" 2>/dev/null | awk -F: '{print $6}' || true)"
fi
if [ -z "$user_home" ]; then
  user_home="$(dscl . -read "/Users/$(id -un)" NFSHomeDirectory 2>/dev/null | awk '{print $2}' || true)"
fi
if [ -z "$user_home" ]; then
  echo "无法确定当前用户的 home 目录，请设置 HOME 后重试。" >&2
  exit 1
fi

config_path="${TMUX_CONF_PATH:-$user_home/.tmux.conf}"
case "$config_path" in
  "~/"*) config_path="$user_home/${config_path#~/}" ;;
esac

assume_yes=0
dry_run=0
install_missing=1
reload_config=1
force_config=0
package_manager=""
package_manager_label=""
plugin_install_failed=0
plugin_failures=()

info() { printf '[%s] %s\n' "$SCRIPT_NAME" "$*"; }
warn() { printf '[%s] 警告：%s\n' "$SCRIPT_NAME" "$*" >&2; }
die() { printf '[%s] 错误：%s\n' "$SCRIPT_NAME" "$*" >&2; exit 1; }

usage() {
  cat <<'USAGE'
用法：tmux-setup.sh [选项]

自动检测系统，安装 tmux/git（如缺失），备份并安装模板配置，获取/更新 TPM
及其插件的最新默认分支，然后校验并重载现有 tmux server。

选项：
  -y, --yes          非交互运行；同意安装依赖并覆盖已有配置
      --force        覆盖已有配置（等同于配置覆盖部分的确认）
      --no-install   缺少 tmux/git 时不安装，直接给出提示并退出
      --no-reload    不重载当前 tmux server
      --dry-run      只检查并显示计划，不写入文件、不安装依赖、不更新插件
      --config PATH  覆盖默认配置路径（默认：~/.tmux.conf）
  -h, --help         显示帮助

示例：
  ./tmux-setup.sh                 # 交互式安装/更新
  ./tmux-setup.sh --yes            # 适合新机器的自动化安装
  ./tmux-setup.sh --dry-run        # 先检查环境
USAGE
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    -y|--yes) assume_yes=1 ;;
    --force) force_config=1 ;;
    --no-install) install_missing=0 ;;
    --no-reload) reload_config=0 ;;
    --dry-run) dry_run=1 ;;
    --config)
      [ "$#" -ge 2 ] || die "--config 需要一个路径参数。"
      config_path="$2"
      case "$config_path" in
        "~/"*) config_path="$user_home/${config_path#~/}" ;;
      esac
      shift
      ;;
    -h|--help) usage; exit 0 ;;
    *) die "未知选项：$1（使用 --help 查看用法）" ;;
  esac
  shift
done

command_exists() { command -v "$1" >/dev/null 2>&1; }

confirm() {
  local prompt="$1"
  if [ "$assume_yes" -eq 1 ]; then
    return 0
  fi
  if [ ! -t 0 ]; then
    warn "$prompt（当前不是交互终端；请使用 --yes 明确授权。）"
    return 1
  fi
  printf '%s [y/N] ' "$prompt"
  local answer
  IFS= read -r answer || true
  case "$answer" in
    y|Y|yes|YES|Yes) return 0 ;;
    *) return 1 ;;
  esac
}

detect_system() {
  local kernel="$(uname -s 2>/dev/null || printf unknown)"
  local distro=""
  if [ "$kernel" = "Linux" ] && [ -r /etc/os-release ]; then
    distro="$(awk -F= '$1 == "ID" {gsub(/"/, "", $2); print $2}' /etc/os-release | head -n 1)"
  fi

  case "$kernel" in
    Darwin)
      info "检测到 macOS。"
      if command_exists brew; then
        package_manager="brew"; package_manager_label="Homebrew"
      elif [ -x /opt/homebrew/bin/brew ]; then
        package_manager="/opt/homebrew/bin/brew"; package_manager_label="Homebrew"
      elif [ -x /usr/local/bin/brew ]; then
        package_manager="/usr/local/bin/brew"; package_manager_label="Homebrew"
      elif command_exists port; then
        package_manager="port"; package_manager_label="MacPorts"
      elif [ -x /opt/local/bin/port ]; then
        package_manager="/opt/local/bin/port"; package_manager_label="MacPorts"
      fi
      ;;
    Linux)
      info "检测到 Linux${distro:+（$distro）}。"
      if command_exists apt-get; then
        package_manager="apt-get"; package_manager_label="APT"
      elif command_exists dnf; then
        package_manager="dnf"; package_manager_label="DNF"
      elif command_exists yum; then
        package_manager="yum"; package_manager_label="YUM"
      elif command_exists pacman; then
        package_manager="pacman"; package_manager_label="Pacman"
      elif command_exists zypper; then
        package_manager="zypper"; package_manager_label="Zypper"
      elif command_exists apk; then
        package_manager="apk"; package_manager_label="APK"
      elif command_exists brew; then
        package_manager="brew"; package_manager_label="Homebrew"
      fi
      ;;
    *)
      warn "暂不识别的系统内核：$kernel。"
      ;;
  esac
}

run_privileged() {
  if [ "$(id -u)" -eq 0 ]; then
    "$@"
  elif command_exists sudo; then
    sudo "$@"
  else
    return 127
  fi
}

install_packages() {
  local packages=("$@")
  [ "$dry_run" -eq 1 ] && { info "[dry-run] 将通过 $package_manager_label 安装：${packages[*]}"; return 0; }
  [ -n "$package_manager" ] || return 127
  case "$package_manager" in
    apt-get)
      run_privileged apt-get update
      run_privileged apt-get install -y "${packages[@]}"
      ;;
    dnf) run_privileged dnf install -y "${packages[@]}" ;;
    yum) run_privileged yum install -y "${packages[@]}" ;;
    pacman) run_privileged pacman -Sy --noconfirm "${packages[@]}" ;;
    zypper) run_privileged zypper --non-interactive install "${packages[@]}" ;;
    apk) run_privileged apk add "${packages[@]}" ;;
    brew|*/brew) "$package_manager" install "${packages[@]}" ;;
    port|*/port) run_privileged "$package_manager" install "${packages[@]}" ;;
    *) return 127 ;;
  esac
}

ensure_dependencies() {
  local missing=()
  command_exists tmux || missing+=(tmux)
  command_exists git || missing+=(git)

  if [ "${#missing[@]}" -eq 0 ]; then
    info "已找到 tmux（$(tmux -V)）和 git。"
    return 0
  fi

  warn "缺少依赖：${missing[*]}。"
  if [ "$install_missing" -eq 0 ]; then
    die "已通过 --no-install 禁止自动安装。请手动安装 tmux 和 git 后重试。"
  fi
  if [ "$dry_run" -eq 1 ]; then
    info "[dry-run] 不会安装缺少的依赖。"
    return 0
  fi
  [ -n "$package_manager" ] || die "找不到可用包管理器。Ubuntu/Debian 请安装 apt，macOS 请安装 Homebrew 或 MacPorts。"
  confirm "是否使用 $package_manager_label 安装 ${missing[*]}？" || die "未获得安装确认。"
  install_packages "${missing[@]}" || die "通过 $package_manager_label 安装依赖失败。请按提示手动安装后重试。"
  command_exists tmux || die "安装完成后仍找不到 tmux。请确认 PATH，然后重试。"
  command_exists git || die "安装完成后仍找不到 git。请确认 PATH，然后重试。"
  info "依赖安装完成：$(tmux -V)。"
}

render_config() {
  local rendered
  rendered="$(mktemp "${TMPDIR:-/tmp}/tmux-setup-config.XXXXXX")" || die "无法创建临时配置文件。"
  if ! cp "$TEMPLATE_PATH" "$rendered"; then
    rm -f "$rendered"
    die "无法读取模板：$TEMPLATE_PATH"
  fi
  printf '%s\n' "$rendered"
}

install_config() {
  local rendered="$1"
  if [ "$dry_run" -eq 1 ]; then
    if [ -e "$config_path" ] && cmp -s "$rendered" "$config_path"; then
      info "[dry-run] 当前配置已与模板一致：$config_path"
    elif [ -e "$config_path" ]; then
      info "[dry-run] 将备份并替换已有配置：$config_path"
    else
      info "[dry-run] 将创建配置：$config_path"
    fi
    rm -f "$rendered"
    return 0
  fi

  if [ -e "$config_path" ] && cmp -s "$rendered" "$config_path"; then
    info "配置已是最新，无需覆盖：$config_path"
    rm -f "$rendered"
    return 0
  fi

  if [ -e "$config_path" ]; then
    if [ "$force_config" -eq 0 ]; then
      confirm "已有配置将被备份后替换：$config_path，继续吗？" || {
        rm -f "$rendered"
        die "已取消配置替换。"
      }
    fi
    local backup_path="${config_path}.backup.$(date +%Y%m%d-%H%M%S)"
    local suffix=0
    while [ -e "$backup_path" ]; do
      suffix=$((suffix + 1))
      backup_path="${config_path}.backup.$(date +%Y%m%d-%H%M%S).$suffix"
    done
    cp -p "$config_path" "$backup_path" || { rm -f "$rendered"; die "备份配置失败：$config_path"; }
    info "已备份旧配置：$backup_path"
  else
    if [ "$force_config" -eq 0 ]; then
      confirm "将创建 tmux 配置：$config_path，继续吗？" || {
        rm -f "$rendered"
        die "已取消配置创建。"
      }
    fi
  fi

  local config_dir
  config_dir="$(dirname -- "$config_path")"
  mkdir -p "$config_dir" || { rm -f "$rendered"; die "无法创建配置目录：$config_dir"; }
  chmod 600 "$rendered"
  mv -f "$rendered" "$config_path" || { rm -f "$rendered"; die "写入配置失败：$config_path"; }
  chmod 600 "$config_path" 2>/dev/null || true
  info "已安装配置：$config_path"
}

install_or_update_plugin() {
  local repo="$1"
  local name="${repo##*/}"
  local destination="$user_home/.tmux/plugins/$name"
  local url="https://github.com/$repo.git"

  if [ "$dry_run" -eq 1 ]; then
    if [ -d "$destination/.git" ]; then
      info "[dry-run] 将更新插件：$repo"
    else
      info "[dry-run] 将克隆插件最新默认分支：$repo"
    fi
    return 0
  fi

  if [ -d "$destination/.git" ]; then
    if [ -n "$(git -C "$destination" status --porcelain 2>/dev/null || true)" ]; then
      warn "插件目录有本地改动，跳过更新以免覆盖：$destination"
      plugin_install_failed=1
      plugin_failures+=("$repo（本地改动）")
      return 0
    fi
    if git -C "$destination" pull --ff-only --tags; then
      info "已更新插件：$repo"
    else
      warn "插件更新失败，保留现有版本：$repo"
      plugin_install_failed=1
      plugin_failures+=("$repo（git pull 失败）")
    fi
    return 0
  fi

  if [ -e "$destination" ]; then
    warn "插件目标路径已存在但不是 Git 仓库，跳过：$destination"
    plugin_install_failed=1
    plugin_failures+=("$repo（目标路径冲突）")
    return 0
  fi
  mkdir -p "$(dirname -- "$destination")"
  if git clone --depth 1 "$url" "$destination"; then
    info "已安装插件最新默认分支：$repo"
  else
    warn "插件克隆失败：$repo"
    plugin_install_failed=1
    plugin_failures+=("$repo（git clone 失败）")
  fi
}

install_plugins() {
  local plugins=(
    "tmux-plugins/tpm"
    "tmux-plugins/tmux-sensible"
    "tmux-plugins/tmux-resurrect"
    "tmux-plugins/tmux-continuum"
    "tmux-plugins/tmux-yank"
    "dracula/tmux"
  )
  if [ "$dry_run" -eq 0 ]; then
    mkdir -p "$user_home/.tmux/plugins" || die "无法创建插件目录：$user_home/.tmux/plugins"
  fi
  local plugin
  for plugin in "${plugins[@]}"; do
    install_or_update_plugin "$plugin"
  done
}

validate_config() {
  [ "$dry_run" -eq 1 ] && { info "[dry-run] 跳过 tmux 配置校验。"; return 0; }
  local socket_name="tmux-setup-check-$$"
  local output
  if output="$(tmux -f "$config_path" -L "$socket_name" start-server 2>&1)"; then
    tmux -L "$socket_name" kill-server >/dev/null 2>&1 || true
    info "tmux 配置校验通过。"
  else
    tmux -L "$socket_name" kill-server >/dev/null 2>&1 || true
    printf '%s\n' "$output" >&2
    die "tmux 配置校验失败，请检查 $config_path。"
  fi
}

reload_tmux() {
  [ "$reload_config" -eq 1 ] || { info "按 --no-reload 要求，不重载当前 tmux server。"; return 0; }
  [ "$dry_run" -eq 1 ] && { info "[dry-run] 若存在 tmux server，将重载 $config_path。"; return 0; }
  if tmux list-sessions >/dev/null 2>&1; then
    if tmux source-file "$config_path"; then
      info "已重载当前 tmux server。"
    else
      warn "配置已写入，但重载当前 tmux server 失败；新会话会使用新配置。"
    fi
  else
    info "当前没有运行中的 tmux server；下次启动 tmux 时会读取新配置。"
  fi
}

main() {
  [ -r "$TEMPLATE_PATH" ] || die "找不到配置模板：$TEMPLATE_PATH"
  detect_system
  ensure_dependencies
  local rendered
  rendered="$(render_config)"
  install_config "$rendered"
  install_plugins
  validate_config
  reload_tmux

  if [ "$plugin_install_failed" -eq 1 ]; then
    warn "部分插件未能更新/安装：${plugin_failures[*]}"
    warn "配置仍已安装；联网后重新运行脚本即可重试。"
    return 1
  fi
  info "tmux 配置完成。插件未固定版本，后续运行会跟随各仓库默认分支更新。"
}

main "$@"
