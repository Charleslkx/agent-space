#!/bin/bash
# 每日折扣模型排名检查脚本
# 1. 运行 ranking 脚本，将结果追加到 log
# 2. 调用 Claude 读取 log，判断是否有重要变化，发送通知

set -euo pipefail

SCRIPT_DIR="/home/ubuntu/github-repository/agent-space/scripts/discounted-model-ranker"
LOG_FILE="${SCRIPT_DIR}/discounted_models.log"  # ranking 脚本写入的 log
LOCK_FILE="/tmp/daily_ranking.lock"
PROMPT_FILE="${SCRIPT_DIR}/scripts/CLAUDE_PROMPT.md"

# 防止并发执行
exec 200>"$LOCK_FILE"
flock -n 200 || { echo "另一个实例正在运行，跳过"; exit 0; }

echo "[$(date '+%Y-%m-%d %H:%M:%S')] 开始运行每日排名检查..."

# 运行 ranking 脚本（会自动写入 discounted_models.log）
cd "$SCRIPT_DIR"
PYTHONPATH=src python3 -m discounted_model_ranker > /dev/null 2>&1

if [ $? -eq 0 ]; then
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] Ranking 完成"

    # 读取 Claude 提示
    PROMPT=$(cat "$PROMPT_FILE")

    # 调用 Claude 分析并发送通知
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] 调用 Claude 分析..."
    claude -p "$PROMPT" \
        --allowedTools "Bash,Read" \
        --output-format text 2>/dev/null

    echo "[$(date '+%Y-%m-%d %H:%M:%S')] Claude 分析完成"
else
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] Ranking 运行失败" >&2
    exit 1
fi

echo "[$(date '+%Y-%m-%d %H:%M:%S')] 完成"
