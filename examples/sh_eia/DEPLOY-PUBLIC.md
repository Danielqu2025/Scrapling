# 上海环评资料检索 — 公网部署指南（Ubuntu + Cloudflare）

本文说明如何在 Ubuntu 上部署 `examples/sh_eia`，经 Cloudflare 托管域名对外提供 HTTPS 访问，并启用 **JWT 认证 + 审批制注册**。

域名请将下文中的 `YOUR_DOMAIN` 替换为实际主机名（例如 `eia.example.com`）。

---

## 架构

```
用户浏览器 → Cloudflare (DNS/CDN/WAF) → 源站 Nginx (TLS) → uvicorn :8080 (FastAPI)
                                                    ├─ data/eia.db   （业务数据）
                                                    └─ data/auth.db  （用户与审计）
```

- 本地开发默认 **关闭** 认证（`SH_EIA_AUTH_ENABLED=0`）
- 公网生产必须 **开启** 认证，并设置强随机 `SH_EIA_JWT_SECRET` 与管理员账号

---

## 1. 服务器准备

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip nginx git
```

将代码放到例如 `/opt/scrapling`，并安装依赖：

```bash
cd /opt/scrapling
python3 -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -e ".[fetchers]"
python -m scrapling.cli install   # 若需要官网同步（Playwright）

cd examples/sh_eia
pip install -r requirements.txt
```

---

## 2. 环境变量

```bash
cd /opt/scrapling/examples/sh_eia
cp .env.example .env
chmod 600 .env
nano .env
```

生产建议：

```env
SH_EIA_HOST=127.0.0.1
SH_EIA_PORT=8080
SH_EIA_AUTH_ENABLED=1
SH_EIA_JWT_SECRET=<用 openssl rand -hex 32 生成>
SH_EIA_ADMIN_USERNAME=admin
SH_EIA_ADMIN_PASSWORD=<强密码>
SH_EIA_SYNC_HOURS=24
SH_EIA_STARTUP_CHECK=1
SH_EIA_STARTUP_CHECK_MODE=remind
```

> 应用只监听本机 `127.0.0.1`，由 Nginx 对外暴露。

---

## 3. 启动应用

前台试跑：

```bash
chmod +x run.sh
./run.sh
```

systemd 示例 `/etc/systemd/system/sh-eia.service`：

```ini
[Unit]
Description=Shanghai EIA search app
After=network.target

[Service]
Type=simple
User=www-data
WorkingDirectory=/opt/scrapling/examples/sh_eia
EnvironmentFile=/opt/scrapling/examples/sh_eia/.env
ExecStart=/opt/scrapling/.venv/bin/python /opt/scrapling/examples/sh_eia/04_run_server.py
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now sh-eia
sudo systemctl status sh-eia
curl -s http://127.0.0.1:8080/health
```

---

## 4. Cloudflare

1. 将 `YOUR_DOMAIN` 的 A/AAAA 记录指向源站公网 IP，代理状态为 **Proxied**（橙色云）
2. SSL/TLS 模式选 **Full (strict)**
3. 建议开启：Always Use HTTPS、Automatic HTTPS Rewrites、Bot Fight Mode（按需）
4. 源站证书任选其一：
   - Cloudflare Origin Certificate（推荐，仅信任 Cloudflare 回源）
   - Let's Encrypt 公有证书

将证书放到例如：

```text
/etc/ssl/sh_eia/origin.crt
/etc/ssl/sh_eia/origin.key
```

---

## 5. Nginx

```bash
sudo cp deploy/nginx.sh_eia.public.conf /etc/nginx/sites-available/sh_eia
sudo sed -i 's/YOUR_DOMAIN/eia.example.com/g' /etc/nginx/sites-available/sh_eia
# 按实际路径修改 ssl_certificate / ssl_certificate_key
sudo ln -sf /etc/nginx/sites-available/sh_eia /etc/nginx/sites-enabled/sh_eia
sudo nginx -t
sudo systemctl reload nginx
```

配置说明见 [deploy/nginx.sh_eia.public.conf](deploy/nginx.sh_eia.public.conf)：HTTPS、安全响应头、反代、WebSocket/长连接预留、大文件上传上限。

防火墙仅放行 80/443（以及你的 SSH 端口），**不要**对公网开放 8080。

---

## 6. 首次使用（审批制）

1. 浏览器打开 `https://YOUR_DOMAIN/login`
2. 使用 `.env` 中的管理员账号登录
3. 其他同事在「申请注册」提交账号 → 状态为 pending
4. 管理员打开 `https://YOUR_DOMAIN/admin`：批准 / 停用 / 激活 / 重置密码
5. 审批通过后普通用户可登录，使用搜索、下载、同步、导入导出

权限约定：

| 能力 | 已激活用户 | 管理员 |
|------|------------|--------|
| 搜索 / 下载 / 同步 / 导入导出 | 是 | 是 |
| 用户审批与审计 | 否 | 是 |

---

## 7. 安全建议

- `SH_EIA_JWT_SECRET` 与管理员密码足够长且唯一；泄露后立即轮换并重启
- `.env` 与 `data/auth.db` 权限限制为服务账户可读
- 定期备份 `data/eia.db` 与 `data/auth.db`（业务库导入导出 **不会** 覆盖 auth.db）
- Cloudflare 可加 IP 访问规则 / WAF 自定义规则限制来源
- 保持系统与 Python 依赖更新；登录接口已做速率限制（slowapi）
- 不要把便携版 exe 直接暴露公网；公网请用本指南的 Nginx + 认证部署

---

## 8. 故障排查

| 现象 | 排查 |
|------|------|
| 服务起不来，日志提示 JWT secret | 开启认证时必须设置 `SH_EIA_JWT_SECRET` |
| 无管理员 | 设置 `SH_EIA_ADMIN_USERNAME` / `SH_EIA_ADMIN_PASSWORD` 后重启 |
| 502 Bad Gateway | `systemctl status sh-eia`；确认监听 `127.0.0.1:8080` |
| Cloudflare 525/526 | 源站证书无效或 SSL 模式不是 Full strict |
| 登录 403 待审批 | 管理员在 `/admin` 批准该用户 |
| 下载 401 | 前端需携带 Token；清除缓存后重新登录 |
| 注册/登录被限流 | 短时间请求过多，稍后再试 |

查看应用日志：

```bash
sudo journalctl -u sh-eia -f
```

---

## 9. 本地对照

Windows：

```powershell
cd examples\sh_eia
copy .env.example .env
.\run.ps1
```

默认 `SH_EIA_AUTH_ENABLED=0`，浏览器直接打开 `http://127.0.0.1:8080` 即可，行为与改造前一致。
