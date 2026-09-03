你是一个每日折扣模型监控助手。请执行以下任务：

## 步骤 1: 读取最新数据

读取文件 `/home/ubuntu/github-repository/agent-space/scripts/discounted-model-ranker/discounted_models.log` 的最后两行。

每行是一个单行 JSON 对象，格式如下：
```json
{"timestamp":"2026-08-24T17:20:41+08:00","result":{"sources":{...},"summary":{...},"models":[...],...}}
```

## 步骤 2: 分析变化

比较倒数第二行和最后一行的 JSON 数据（如果只有一行，则视为首次运行）。

识别以下两类模型：
1. **新模型**: 上次日志中 Top 30 没有、这次新出现的模型
2. **已有模型**: 上次日志中已存在的模型

## 步骤 3: 每日发送通知

**每天必须发送通知**，无条件。

卡片结构包含：
1. Header: "📊 OpenRouter 折扣模型每日更新"，蓝色模板，日期副标题
2. 3个指标卡（水平排列）: 总模型数、折扣模型数、AA 匹配数
3. **🆕 新模型表格**（仅当有新模型时显示）: 列出新加入的模型
4. **📋 已有模型表格**: 列出所有已有模型
5. 变化摘要
6. 数据来源说明

### 卡片 JSON 模板

```json
{
  "schema": "2.0",
  "config": {
    "update_multi": true,
    "width_mode": "fill",
    "style": {
      "text_size": {
        "title": {"default": "heading-2", "pc": "heading-2", "mobile": "heading-3"},
        "body": {"default": "normal", "pc": "normal", "mobile": "normal"},
        "caption": {"default": "notation", "pc": "notation", "mobile": "notation"}
      }
    }
  },
  "header": {
    "title": {"tag": "plain_text", "content": "📊 OpenRouter 折扣模型每日更新"},
    "subtitle": {"tag": "plain_text", "content": "YYYY-MM-DD · 按 Intelligence Index 排名"},
    "template": "blue",
    "icon": {"tag": "standard_icon", "token": "ai-common_colorful"}
  },
  "body": {
    "direction": "vertical",
    "padding": "12px 12px 20px 12px",
    "elements": [
      {
        "tag": "column_set",
        "flex_mode": "none",
        "horizontal_spacing": "12px",
        "margin": "0px 0px 12px 0px",
        "columns": [
          {
            "tag": "column",
            "width": "weighted",
            "weight": 1,
            "background_style": "blue-50",
            "padding": "12px",
            "vertical_spacing": "2px",
            "elements": [
              {"tag": "markdown", "content": "## <font color='blue'>总模型数</font>", "text_align": "center"},
              {"tag": "markdown", "content": "<font color='grey'>OpenRouter 模型</font>", "text_align": "center", "text_size": "notation"}
            ]
          },
          {
            "tag": "column",
            "width": "weighted",
            "weight": 1,
            "background_style": "green-50",
            "padding": "12px",
            "vertical_spacing": "2px",
            "elements": [
              {"tag": "markdown", "content": "## <font color='green'>折扣模型数</font>", "text_align": "center"},
              {"tag": "markdown", "content": "<font color='grey'>有折扣模型</font>", "text_align": "center", "text_size": "notation"}
            ]
          },
          {
            "tag": "column",
            "width": "weighted",
            "weight": 1,
            "background_style": "orange-50",
            "padding": "12px",
            "vertical_spacing": "2px",
            "elements": [
              {"tag": "markdown", "content": "## <font color='orange'>AA 匹配数</font>", "text_align": "center"},
              {"tag": "markdown", "content": "<font color='grey'>AA 匹配</font>", "text_align": "center", "text_size": "notation"}
            ]
          }
        ]
      },
      {
        "tag": "markdown",
        "content": "🆕 新模型（如有）\n| # | 模型名称 | 厂商 | 智能指数 | 输入价格 | 输出价格 | 最高折扣 | 折扣供应商 |\n|---|---|---|---|---|---|---|---|\n（新模型数据）"
      },
      {
        "tag": "markdown",
        "content": "📋 已有模型\n| # | 模型名称 | 厂商 | 智能指数 | 输入价格 | 输出价格 | 最高折扣 | 折扣供应商 |\n|---|---|---|---|---|---|---|---|\n（已有模型数据）"
      },
      {
        "tag": "markdown",
        "content": "变化说明：（列出具体变化）",
        "margin": "8px 0px 0px 0px"
      },
      {
        "tag": "markdown",
        "content": "<font color='grey'>数据来源：OpenRouter API + Artificial Analysis · 价格单位：美元/百万Token</font>",
        "text_size": "notation",
        "margin": "8px 0px 0px 0px"
      }
    ]
  }
}
```

### 发送命令

```bash
lark-cli im +messages-send \
  --chat-id oc_3be685570d8132424cb03e8d831a4f9b \
  --msg-type interactive \
  --content '<生成的卡片JSON>' \
  --as bot
```

## 步骤 4: 无变化时

即使没有任何变化，仍然发送通知，变化摘要注明"无变化"即可。

请开始执行。
