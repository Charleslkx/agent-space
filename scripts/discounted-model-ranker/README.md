# Discounted Model Ranker

查询 OpenRouter 当前折扣端点，与 Artificial Analysis 语言模型数据匹配，按 Intelligence Index 输出前 30 名。每次成功查询写入 `discounted_models.log`，仅保留最近 7 次。

## 要求

- Python 3.10+
- `curl`
- Artificial Analysis API key

## 安装

```bash
python -m pip install .
```

复制 `.env.example` 为 `.env` 并填入 key；也可设置环境变量 `AA_API_KEY`。

## 使用

```bash
discounted-model-ranker
discounted-model-ranker --self-test
```

结果以 JSON 输出到标准输出，日志写入运行命令时的当前目录。构建分发包：

```bash
python -m pip install build
python -m build
```

Artificial Analysis 数据使用时需注明来源：https://artificialanalysis.ai/ 。
