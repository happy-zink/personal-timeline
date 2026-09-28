# AI / Agent 接口文档

本机服务默认：`http://127.0.0.1:8764`  
机器可读契约：`GET /api/openapi.json`

## 设计原则

1. **自然语言优先**：人说人话就能记账；Agent 也可直接给结构化 JSON。
2. **缺失不装 0**：没写的字段就是缺失，`null` 或省略。
3. **日期是本地日历日**：`YYYY-MM-DD`，不做 UTC 切日。
4. **跨午夜睡眠记起床日**。
5. **可预览可撤销**：`confirm:false` 只解析；写入前也可走日记录页改。

## 接口一览

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| POST | `/api/ai/ingest` | 自然语言或结构化写入（主入口） |
| POST | `/api/ai/parse` | 只解析返回 draft，不写库 |
| POST | `/api/ai/batch` | 批量补历史 |
| GET/PUT/DELETE | `/api/entries/{date}` | 读/写/删某天 |
| GET | `/api/metrics` | 指标列表（含自定义） |
| GET | `/api/trends` | 序列（缺失为 null） |
| GET | `/api/export/csv` · `/api/export/json` | 完整导出 |
| POST | `/api/restore` | 备份恢复 `replace\|merge` |
| GET | `/api/openapi.json` | OpenAPI 3 契约 |

## 自然语言示例

```json
POST /api/ai/ingest
{
  "text": "今天体重68.5公斤，睡了7个半小时，心情不错，跑步了30分钟，有点累",
  "confirm": true
}
```

可识别（离线规则解析，不依赖外网模型）：

- 相对日期：今天 / 昨天 / 前天 / N天前 / 9月28日 / 2026-09-28
- 体重：`68.5公斤` / `68kg` / `136斤`（自动换算 kg）/ `150lb`
- 睡眠：`睡了7个半小时` / `睡眠7.5h` / `只睡了5小时`
- 心情：数字 1–5，或「很开心 / 不错 / 一般 / 很差 / 崩溃」
- 睡眠感受：「睡得不错 / 失眠 / 浅睡 / 深睡」
- 运动：`跑步30分钟` / `运动了1.5小时` / `快走40分钟`
- 鼾声：`鼾声12`

## 结构化示例

```json
POST /api/ai/ingest
{
  "date": "2026-09-28",
  "values": {
    "weight": 68.5,
    "sleep_hours": 7.5,
    "sleep_quality": 4,
    "mood": 4,
    "exercise_minutes": 30,
    "exercise_type": "跑步",
    "snore_burden": 12
  },
  "note": "由外部 Agent 写入",
  "timezone": "Asia/Shanghai"
}
```

内置指标 key：

`weight`(kg) · `sleep_hours` · `sleep_quality`(1-5) · `mood`(1-5) · `exercise_minutes` · `exercise_type` · `snore_burden` · `sleep_note`

自定义指标可通过 `POST /api/metrics` 创建后同样写入。

## 批量

```json
POST /api/ai/batch
{
  "items": [
    {"text": "前天体重68kg，睡了6小时"},
    {"date": "2026-09-20", "values": {"mood": 3}, "note": "加班"}
  ]
}
```

## 并发 / 多用户（诚实说明）

- **没有注册登录**：这是单用户本地工具。数据属于这台机器上的你。
- **并发安全**：SQLite WAL + busy_timeout；多线程 HTTP。多标签页、多脚本同时读写可用。
- **多用户**：若要家庭多成员，需自行在前面加鉴权，并按用户分库。当前版本刻意不提供，避免云端复杂度破坏隐私承诺。

## 快速自测

```bash
curl -X POST http://127.0.0.1:8764/api/ai/parse \
  -H "Content-Type: application/json" \
  -d '{"text":"今天体重68kg，睡了7小时，心情不错"}'
```

```bash
python tests/test_ai_ingest.py
```
