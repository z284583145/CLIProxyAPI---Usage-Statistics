# CLIProxyAPI 用量统计面板部署与发布验证

最后更新：2026-05-20

## 适用范围

本文档用于发布 `cliproxyapi-usage-dashboard` 到本机或受控内网机器。项目没有第三方运行时依赖，生产运行只需要 Python 3.9+ 标准库、可访问的 CLIProxyAPI Management API，以及本机 Codex OAuth 凭证文件。

## 发布前检查

1. 确认分支干净且只包含本次发布改动：

```bash
git status --short
git diff --stat
```

2. 清理不会提交的运行产物：

```bash
rm -rf __pycache__ tests/__pycache__
```

Windows PowerShell：

```powershell
Remove-Item -Recurse -Force __pycache__, tests\__pycache__ -ErrorAction SilentlyContinue
```

`logs/`、`*.sqlite`、`config.local.json` 必须保持在 `.gitignore` 范围内，不进入提交。

3. 确认配置模板仍是脱敏值：

```bash
cat config.json
```

模板中的 `management_key` 必须是 `replace-with-your-management-key`，生产密钥只放在运行目录配置或 `CLIPROXY_MANAGEMENT_KEY` 环境变量中。

4. 执行敏感信息扫描：

```bash
git grep -n -I "refresh_token\|id_token\|gho_\|Bearer [A-Za-z0-9]\|chatgpt_account_id"
```

允许命中文档中的示例命令和源码中的字段名；如果命中真实 token、真实邮箱、真实 key 或真实数据库内容，必须停止发布并清理。

## 构建与验证

项目没有前端构建步骤。发布前至少执行：

```bash
python -m unittest discover -s tests -p "test_*.py" -v
python -m compileall usage_dashboard.py tests
python usage_dashboard.py init
python usage_dashboard.py report today
```

如需验证网页和 API，启动本地服务：

```bash
python usage_dashboard.py serve
```

然后检查：

```text
GET http://127.0.0.1:8320/api/health
GET http://127.0.0.1:8320/api/summary?period_type=day&period_key=2026-05-20
GET http://127.0.0.1:8320/api/summary?period_type=month&period_key=2026-05
GET http://127.0.0.1:8320/api/summary?period_type=year&period_key=2026
GET http://127.0.0.1:8320/api/requests?limit=20&period_type=day&period_key=2026-05-20
GET http://127.0.0.1:8320/api/collector-status
```

浏览器冒烟验证：

- 打开 `http://127.0.0.1:8320/`，确认页面首屏、KPI、图表和表格渲染。
- 切换日期选择器的日、月、年，确认请求列表跟随筛选。
- 点击顶部“刷新”，确认普通刷新完成且有 toast 反馈。
- 点击账号余量面板刷新按钮，确认 `/api/quota?force=1` 行为符合预期；没有真实 OAuth 凭证时允许返回空列表或外部接口错误。

兼容性验证：

- Windows：双击 `start_dashboard.cmd` 或运行 `usage_dashboard.py run`，确认同一进程内同时启动采集器 watchdog 和面板服务。
- macOS：用 `launchd/*.plist` 模板部署前，必须把 `/Users/YOUR_USER` 替换为真实 home 路径。
- 浏览器：至少验证 Chromium/Chrome；页面使用原生 HTML/CSS/Canvas/Fetch，不依赖第三方前端包。

## 生产部署

1. 将源码和模板同步到目标机器。
2. 复制运行文件：

```bash
mkdir -p ~/.cli-proxy-api/usage-dashboard
cp usage_dashboard.py ~/.cli-proxy-api/usage-dashboard/
cp config.json ~/.cli-proxy-api/usage-dashboard/config.json
chmod 700 ~/.cli-proxy-api/usage-dashboard
chmod 600 ~/.cli-proxy-api/usage-dashboard/config.json
```

3. 在运行目录配置真实 `management_key`，或设置：

```bash
export CLIPROXY_MANAGEMENT_KEY="your-management-key"
```

4. 初始化并启动：

```bash
python ~/.cli-proxy-api/usage-dashboard/usage_dashboard.py init
python ~/.cli-proxy-api/usage-dashboard/usage_dashboard.py run
```

5. 保持 `dashboard_host` 为 `127.0.0.1`，除非已经额外增加反向代理鉴权和网络访问控制。

## 上线后验证

- `/api/health` 返回 `{"ok": true, ...}`。
- `/api/collector-status` 在 CLIProxyAPI 可用且密钥正确时应转为正常状态；如果 CLIProxyAPI 未启动或密钥错误，应显示采集异常，不影响页面服务。
- `/api/summary`、`/api/requests` 返回 JSON 且无 500。
- SQLite 中 `usage_events.api_key_hash` 可用于聚合，`usage_events.raw_json` 不应包含完整 API key 或 OAuth token。
- `/api/quota` 不应返回 `raw_json`。
- 页面自动刷新 30 秒一次，手动刷新不会触发强制余量刷新；账号余量按钮才会触发 `force=1`。

## 回滚方案

1. 停止当前服务：

```bash
Ctrl+C
```

或按实际守护方式停止 Windows/macOS 后台任务。

2. 切回上一版源码或上一提交：

```bash
git checkout <previous-known-good-commit>
```

3. 重新复制 `usage_dashboard.py` 到运行目录并启动：

```bash
cp usage_dashboard.py ~/.cli-proxy-api/usage-dashboard/
python ~/.cli-proxy-api/usage-dashboard/usage_dashboard.py run
```

4. 数据库 schema 本次发布未新增字段，正常不需要回滚 SQLite。若需完整回退数据状态，先停止服务，再恢复发布前备份的 `usage.sqlite`、`usage.sqlite-wal`、`usage.sqlite-shm`。

5. 回滚后重复“上线后验证”。如果仍异常，优先检查 CLIProxyAPI Management API、`management_key`、本地 OAuth 凭证和端口占用。
