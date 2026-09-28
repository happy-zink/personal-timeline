# HTTPS / 上线一键部署

三种方式，按你的场景选。

## A. 自签证书直连（最快，适合家里局域网）

```powershell
# Windows（PowerShell）
.\deploy\start_https.ps1
```

```bash
# macOS / Linux
./deploy/start_https.sh
```

效果：
- 自动在 `deploy/certs/` 生成 `tls.crt` / `tls.key`（有效期 2 年）
- 服务以 **https://localhost:8764** 启动
- 浏览器首次会告警，点「继续访问」；家人设备同理
- 绑定 `0.0.0.0`，同一 Wi-Fi 下可用 `https://<你的局域网IP>:8764`

自定义：

```powershell
.\deploy\start_https.ps1 -Port 8765 -Hosts "localhost,127.0.0.1,192.168.1.20"
```

## B. Caddy 反向代理 + 可信证书（推荐上线 / 远程给家人）

1. 安装 [Caddy](https://caddyserver.com/download)（下载 `caddy.exe` 放到 `deploy\caddy.exe` 即可，无需安装）
2. 域名 A 记录指向你的服务器，放行 80/443
3. 一键启动：

```powershell
.\deploy\start_https.ps1 -UseCaddy -Domain timeline.你的域名.com
```

```bash
./deploy/start_https.sh --caddy --domain timeline.example.com
```

Caddy 自动申请/续期 Let's Encrypt 证书。模板见 `deploy/Caddyfile`。

仅内网、用 Caddy 自签：

```powershell
.\deploy\start_https.ps1 -UseCaddy
# 访问 https://localhost:8443
```

## C. 手动指定已有证书

```powershell
python server.py --host 0.0.0.0 --port 8764 --https --cert C:\certs\fullchain.pem --key C:\certs\privkey.pem
```

## 端口与防火墙

本脚本**不会**改系统防火墙。若要外网访问，请自行放行对应端口（如 443 或 8764）。

## 健康检查

```bash
curl -k https://localhost:8764/api/health
```

## 全家备份（管理员）

登录 → 「成员」→「一键导出全家备份」得到 zip：

```
manifest.json
app.sqlite              # 账号
users/<id>/timeline.db  # 每人数据
```

恢复：同页「恢复全家备份」。建议先导出再覆盖。

## 邀请注册

1. 管理员「成员」→「生成邀请码」
2. 把 `INV-XXXX` 发给家人
3. 对方打开登录页 →「注册」→ 填邀请码

## 安全提醒

- 自签证书只防窃听，不防中间人冒充；远程家人请用方式 B
- 首个管理员密码立刻修改
- `data/` 目录权限收紧，定期把全家备份 zip 存到外部磁盘
