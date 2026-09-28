# 上线 / 部署说明

## 形态

这是**可自部署的多用户本地服务**，不是 SaaS。数据默认只在你这台机器。

## 最小上线清单

1. **机器**：任意 Windows / Linux / macOS，Python 3.10+
2. **启动**：
   ```bash
   python server.py --host 127.0.0.1 --port 8764
   ```
3. **反向代理 + HTTPS**（给家人远程用时必须）  
   Caddy 示例：
   ```caddy
   timeline.example.com {
       reverse_proxy 127.0.0.1:8764
   }
   ```
4. **账号**：
   - 控制台会打印首个管理员初始密码
   - 登录后立刻修改（成员页 / 重建账号）
   - 为每位家人创建独立 `member` 账号
5. **AI**：每人可在「账号与密钥」配置自己的 OpenAI 兼容 Key
6. **Agent**：每人生成自己的 `tk_` API Key，配置到 MCP / 脚本

## 数据备份

```
data/app.sqlite              # 账号（务必备份）
data/users/<id>/timeline.db  # 每人数据
data/users/<id>/backups/     # 自动 7 份滚动
```

建议：每日把 `data/` 打包到外部磁盘/对象存储。

## 并发与容量

- SQLite WAL + 多线程 HTTP：家庭十人级读写完全够用
- 单库体积到 GB 级仍可接受；导出 JSON/CSV 便于迁移
- 无横向扩展设计；若要千人级需换 PostgreSQL（当前未做）

## 安全边界

| 已做 | 你自己要做 |
| --- | --- |
| 密码 PBKDF2 | HTTPS / 防火墙 / 非 root 运行 |
| 会话 HttpOnly Cookie | 定期改密、吊销丢失的 API Key |
| API Key 哈希存储 | 不要把 `data/` 传到公开仓库 |
| 用户数据目录隔离 | 主机磁盘加密（可选） |

## 升级

替换代码目录后重启进程即可。`app.sqlite` 与 `users/` 保持兼容。
schema 变更会用 `CREATE TABLE IF NOT EXISTS` 演进。
