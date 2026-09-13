# 每日折扣模型排名监控

自动监控 OpenRouter 折扣模型变化，通过 Claude 分析并发送飞书通知。

## 目录结构

```
discounted-model-ranker/
├── src/                          # 主程序源码
├── scripts/
│   ├── daily_check.sh           # 每日检查脚本（cron 调用）
│   └── CLAUDE_PROMPT.md         # Claude 分析提示模板
├── data/
│   ├── daily_ranking.log        # 历史排名数据（增量追加）
│   └── cron.log                 # cron 运行日志
└── README.md
```

## 工作流程

1. **Cron 触发**（北京时间 19:03）
   - 运行 `daily_check.sh`

2. **数据采集**
   - 调用 `discounted_model_ranker` 获取最新数据
   - 结果以 JSON 格式追加到 `data/daily_ranking.log`

3. **智能分析**
   - Claude 读取 log 最后两行，比较变化
   - 判断是否满足通知条件

4. **条件通知**（满足任一）
   - 折扣变化超过 ±10%
   - 新模型进入 Top 10
   - 排名变化超过 3 位
   - 新增高折扣模型（折扣 ≥ 50% 且智能指数 ≥ 40）

5. **发送通知**
   - 以 bot 身份发送飞书卡片到 Agent Notifier 群聊

## 手动运行

```bash
# 仅运行 ranking
PYTHONPATH=src python3 -m discounted_model_ranker

# 运行完整检查（包含 Claude 分析）
./scripts/daily_check.sh
```

## Cron 配置

```bash
# 查看当前 crontab
crontab -l

# 编辑 crontab
crontab -e
```

当前配置：
```
# 每日折扣模型排名检查 - 北京时间 19:03
3 11 * * * /home/ubuntu/github-repository/agent-space/scripts/discounted-model-ranker/scripts/daily_check.sh >> /home/ubuntu/github-repository/agent-space/scripts/discounted-model-ranker/data/cron.log 2>&1
```

## 通知格式

飞书卡片 2.0，包含：
- 📊 标题 + 日期
- 3个指标卡（总模型数 / 折扣模型数 / AA 匹配数）
- Top 10 表格
- 变化说明

## 依赖

- Python 3.10+
- curl
- Artificial Analysis API Key（配置在 .env）
- lark-cli（已配置 bot 身份）
- Claude Code CLI（用于智能分析）
