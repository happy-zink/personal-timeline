# MCP 服务 · 个人数据时光轴

让 Claude / MiMo / 任意支持 MCP 的 Agent **用 API Key 读写你的健康与生活数据**。

## 1. 先准备 API Key

1. 启动服务：`python server.py`
2. 浏览器登录 → 「账号与密钥」→「新建 API Key」
3. 复制明文 `tk_...`（只显示一次）

## 2. 启动 MCP（stdio）

```bash
# Windows PowerShell
$env:TIMELINE_API_KEY = "tk_你的密钥"
$env:TIMELINE_BASE = "http://127.0.0.1:8764"
python mcp\server.py
```

## 3. 注册到 MCP 客户端

在 MiMo Desktop 的 `mimocode.jsonc`（或客户端 MCP 配置）里加入：

```json
{
  "mcp": {
    "personal-timeline": {
      "type": "local",
      "command": ["python", "mcp/server.py"],
      "environment": {
        "TIMELINE_API_KEY": "tk_你的密钥",
        "TIMELINE_BASE": "http://127.0.0.1:8764"
      },
      "enabled": true
    }
  }
}
```

路径请按实际绝对路径调整。重启引擎或新开会话后生效。

## 4. 提供的工具

| 工具 | 说明 |
| --- | --- |
| `ai_ingest` | 自然语言或 JSON 写入一天 |
| `ai_parse` | 只解析不写入 |
| `get_entry` / `put_entry` | 读/写某天 |
| `list_metrics` | 指标字典 |
| `get_trends` | 趋势序列（缺失为 null） |
| `search_notes` | 搜备注 |

## 5. 也可用纯 HTTP（不走 MCP）

```bash
curl -X POST http://127.0.0.1:8764/api/ai/ingest \
  -H "Authorization: Bearer tk_..." \
  -H "Content-Type: application/json" \
  -d '{"text":"今天体重68kg，睡了7小时，心情不错","confirm":true}'
```

契约：`GET /api/openapi.json`

## 数据与隐私

- 每个用户独立库：`data/users/<user_id>/timeline.db`
- API Key 按用户签发，可随时吊销
- 服务只监听 127.0.0.1（上线请自行加 HTTPS 反代与防火墙）
- Agent 只能访问该 Key 对应用户的数据
