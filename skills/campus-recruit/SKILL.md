---
name: campus-recruit
description: >
  Query a Feishu Base for campus recruitment, internship, spring recruitment,
  autumn recruitment, and related job-board records through lark-cli. Use this
  whenever the user gives a Feishu Base link, asks to read a recruitment Base,
  wants records from a named view such as "先看这个表", or needs filtered campus
  recruiting rows by date, location, degree, job type, company, or similar fields.
---

## 项目环境

| 项目 | 值 |
|------|----|
| 项目名 | `campus-recruit` |
| 项目目录 | `.` |
| Python 版本 | `>=3.11` |
| 包管理 | `uv`，lockfile 为 `uv.lock` |
| 虚拟环境 | `.venv/` |
| 默认入口 | `scripts/query_base.py` |
| 环境预检 | `scripts/run_with_env_check.sh` |

这个 skill 依赖外部命令 `lark-cli`。Python 脚本本身只用标准库，不依赖机器上的绝对路径。

## 使用方式

### 快速获取行业分类日报（推荐）

```bash
# 今日数据（如无记录会自动扩展到更长窗口）
./scripts/run_with_env_check.sh python3 scripts/parse_campus_recruit.py --today -n 200 -o text

# 最近30天，JSON格式
./scripts/run_with_env_check.sh python3 scripts/parse_campus_recruit.py --days 30 -n 200 -o json
```

### 注入 PATH 直接运行（当 uv run 找不到 lark-cli 时）

```bash
UV_CACHE_DIR=.uv-cache PATH="$HOME/.npm-global/bin:$PATH" uv run python3 scripts/parse_campus_recruit.py --today -n 200 -o text
```

### 底层查询

```bash
./scripts/run_with_env_check.sh
./scripts/run_with_env_check.sh python3 scripts/query_base.py --as-user --pretty
./scripts/run_with_env_check.sh python3 scripts/query_base.py --as-user --where 招聘类型=暑期实习,实习
```

### 行业分类参考

见 `references/industry_tags.md`，记录了 Base 中实际出现的 46 种 industry 标签组合及其到输出类别的映射规则。

## 默认行为

- Base token: `QupsbMixhaDKiqsc1CTcjJlGnGe`
- 表名: `26届秋招&春招汇总`
- 视图名: `先看这个表`
- 时间字段: `开始时间`
- 时间窗口: 过去 `10` 天，含今天

脚本默认直接把"表名/视图名"传给 `lark-cli`。若用户已经给出 ID，也可以直接用 `--table-id` 或 `--view-id` 覆盖。

### 两段式输出（增量逻辑）

`-o text` 模式下，输出自动分为两段：

1. **🆕 今日新增** — `开始时间` 等于当天的记录
2. **📅 近N日其余** — 窗口内但 `开始时间` 不等于当天的记录

同一家公司可能同时出现在两段（比如今天发了新岗位，之前也有在招岗位）。底部汇总行显示 `合计：X 家公司（今日 Y + 近期 Z）`。

若某段无数据，显示 `（无）`。

## 参数规则

- `--field`：控制返回字段。可重复传。
- `--where 字段=值1,值2`：追加结构化过滤。多值自动使用 `intersects` 操作符。
- `--days`：控制最近 N 天（默认 10）。
- `--group full|simplified`：行业分类模式，`full`=9类（默认），`simplified`=4类（金融/互联网/国央企/其他）。
- `--as-user`：以用户身份调用 `lark-cli`。
- `--identity user|bot`：显式指定身份。
- `--pretty`：格式化 JSON 输出。
- `--dry-run`：只打印解析后的命令和过滤条件。

过滤规则：

- 单值条件会生成精确匹配 `==`
- 多值条件会生成多选相交 `intersects`
- 时间条件始终在 Base 端执行，不在本地做假过滤

## 分发约束

- 不要写死 skill 的绝对路径。始终从当前 skill 根目录使用相对路径，如 `./scripts/run_with_env_check.sh`
- 不要假设用户机器上已有 `.venv`、`uv.lock`、或现成 Python 环境
- 只要本机有 `uv`，就优先用 `uv`
- `UV_CACHE_DIR` 必须落在项目目录内，避免依赖 `~/.cache/uv`
- 若执行失败，优先返回真实错误，不要伪造“权限不足”或“数据为空”之类解释

## 运行前置：PATH 与虚拟环境

`uv run` 不会继承 shell 的自定义 PATH（如 `~/.npm-global/bin`），因此脚本内调用 `lark-cli` 会找不到。必须显式注入 PATH：

```bash
UV_CACHE_DIR=.uv-cache PATH="$HOME/.npm-global/bin:$PATH" uv run python3 scripts/query_base.py --as-user --pretty
```

同时，`VIRTUAL_ENV` 环境变量会与 `uv run` 冲突（hermes-agent 的 venv 会遮蔽项目的 `.venv`），`UV_CACHE_DIR=.uv-cache` 可以避免这个问题。

## 失败处理

常见失败点：

- `lark-cli not found in PATH`
  说明未安装或未加入 PATH。若已安装但不在默认 PATH 中，需在命令前加 `PATH="$HOME/.npm-global/bin:$PATH"`
- `keychain not initialized`
  说明当前环境拿不到 Feishu 凭据，通常需要在可访问系统钥匙串的环境里运行
- `permission_violations` / `missing_scope`
  说明 `lark-cli` 已登录，但当前身份缺少 scope
- `Table not found` 或 `View not found`
  说明默认名字失效，或用户给错了目标 Base
- `--today` / `--days 1` 返回空结果
  招聘公告的`开始时间`并非每天都有新记录。这是正常现象，`--days 1` 返回空不代表系统异常。用 `--days 30` 获取近期数据更可靠。cron
  日报脚本推荐用 `--days 30` 然后取当天新发布的条目，或直接输出最近 30 天的全部记录。

## 行业分类

`scripts/parse_campus_recruit.py` 在 `-o text` 模式下支持两种行业分组：

- **`--group full`**（默认）：9 个行业类别（互联网/科技、金融/银行、消费/零售、制造/工业、汽车、能源/环保、航空/航天、教育、其他）。
- **`--group simplified`**：4 个大类（金融、互联网、国央企、其他），优先级为 金融 > 互联网 > 国央企 > 其他。国央企类包含带"国央企/央企/国企/事业单位"标签的公司。

分类规则和标签分布见 `references/industry_tags.md`。

## Cron Job 日报模式

推荐使用 `scripts/parse_campus_recruit.py --days 10 --group simplified --where "招聘类型=秋招,秋招提前批,暑期实习,实习" -n 200 -o text` 作为 cron job 入口：

- `--days 10`：近 10 天（含今天）
- `--group simplified`：4 大类分组（金融→互联网→国央企→其他）
- `--where "招聘类型=..."`：按招聘类型过滤
- 脚本自动推断 identity 为 `user`
- 输出直接可作为日报发送
- 如需旧版行为（底层 JSON 查询/9 类分组），仍可用 `scripts/query_base.py` 或 `--group full`

出现失败时：

- 停止继续执行
- 保留原始错误主信息
- 若是认证相关错误，补充兜底提示：
  - `lark-cli config init`
  - `lark-cli auth login --scope "<missing_scope>"`
  - macOS 沙箱场景下可在交互终端执行 `lark-cli config keychain-downgrade`
