# 私人数据时光轴

**多用户 · 本地优先 · AI 可写入 · MCP 接入** 的个人/家庭健康与生活数据时间轴。

不是演示：支持注册登录、家人分账号、数据分库、API Key、真实 LLM、MCP Agent 调用、CSV/JSON/月报导出与备份恢复。

## 产品定位

| | 云健康 App | Excel/手账 | 本产品 |
| --- | --- | --- | --- |
| 数据归属 | 厂商云端 | 散落文件 | **本机分用户 SQLite，整库可搬** |
| 多用户 | 家庭共享难 | 无 | **注册 / 成员 / 权限 / 密钥** |
| 录入 | 手点 | 手点 | **人话 + 表单 + CSV + Agent** |
| 对 AI 开放 | 通常无 | 无 | **HTTP API + MCP + OpenAPI** |
| 缺失值 | 不透明 | 常被当 0 | **null 诚实，图表断开** |
| 费用与锁定 | 订阅 | — | **可自部署，无强制云** |

## 一键启动

```powershell
.\start.ps1
```

打开 http://127.0.0.1:8764

- 首次启动自动创建管理员并在控制台打印初始密码（也可自行注册，若库为空则首个用户为管理员）。
- 测试环境当前管理员：`admin` / `Admin12345`（**上线请立刻改掉**）。

依赖：Python 3.10+ 标准库。

## 多用户与数据目录

```
data/
  app.sqlite                 # 账号、会话、API Key、AI 配置
  users/
    <user_id>/
      timeline.db            # 该用户全部指标/备注/月报
      backups/auto-*.json    # 自动备份（保留 7 份）
```

- 成员之间数据完全隔离（独立库文件）。
- 管理员在「成员」页添加家人，分配初始密码。
- 每人可生成自己的 API Key。

## AI：两层

1. **真 LLM（推荐）**  
   「账号与密钥」里填任意 **OpenAI 兼容** `API Base + Key + Model`。  
   自然语言会先送模型抽取结构化字段，失败自动回退规则解析。

2. **离线规则回退**  
   无 Key / 断网 / 超时仍可记账（支持 今天/昨天/斤/kg/半小时/心情词…）。

首页一句话示例：

> 昨天体重136斤，只睡了5个小时，心情很差，没运动

## Agent / MCP

```bash
$env:TIMELINE_API_KEY="tk_..."
$env:TIMELINE_BASE="http://127.0.0.1:8764"
python mcp\server.py
```

工具：`ai_ingest` / `ai_parse` / `get_entry` / `put_entry` / `list_metrics` / `get_trends` / `search_notes`  
配置样例见 `mcp/README.md`。HTTP 契约：`GET /api/openapi.json`

## 鉴权

| 方式 | 用法 |
| --- | --- |
| 浏览器 | Cookie 会话（登录后） |
| 脚本 / Agent / MCP | `Authorization: Bearer tk_...` |

- API Key 可只给 `read` 或 `read,write`
- 可随时吊销
- 明文只在创建时显示一次

## 主要功能

- 首页：AI 一句话录入、今日快照、趋势、待补日期、月历
- 日记录：体重/睡眠/心情/运动/鼾声/自定义指标，范围校验，改历史
- 日历 / 趋势 / 月报（Markdown + 打印）
- CSV 导入：预览、字段映射、单位、重复策略（绝不静默覆盖）
- 项目 01 打鼾 CSV 自动映射
- JSON 备份 / 恢复（replace / merge）

## 样例与测试

```powershell
$env:ADMIN_USER="admin"; $env:ADMIN_PASS="Admin12345"
python tests\test_multiuser.py     # 多用户/隔离/API Key
python tests\test_ai_ingest.py     # 自然语言
python tests\test_flow.py          # 端到端
python tests\test_import_strategies.py
```

样例在 `samples/`，为虚构测试数据。

## 上线 / 远程给家人用

**一键 HTTPS**（详见 `deploy/README.md`）：

```powershell
# 自签证书直连（局域网）
.\deploy\start_https.ps1

# Caddy + 可信证书（远程推荐）
.\deploy\start_https.ps1 -UseCaddy -Domain timeline.你的域名.com
```

```bash
./deploy/start_https.sh
./deploy/start_https.sh --caddy --domain timeline.example.com
```

**全家备份**：管理员 →「成员」→「一键导出全家备份」（zip 含账号+每人数据），可同页恢复。

**邀请码注册**：管理员生成 `INV-XXXX` 发给家人，对方登录页填码自助开通。

## 数据与备份

```
data/app.sqlite              # 账号、API Key、邀请码
data/users/<id>/timeline.db  # 每人数据
data/users/<id>/backups/     # 自动 7 份
deploy/certs/                # 自签证书（如启用 HTTPS）
```

## 已知限制

见 `docs/KNOWN_LIMITS.md`。接口见 `docs/API.md`。测试记录 `docs/TESTING.md`。
