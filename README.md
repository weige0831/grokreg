# grokreg

Grok 注册 → **Build 通道 grok-4.5 硬测活** → 仅成功号写入 **grok2api Build 池**。

邮箱：[tempmail-server](https://github.com/Lm36/tempmail-server)  
Base：`https://mail.minecraft-cn.net`  
域名（随机）：`mtoosov.shop` / `olsbvgq.shop` / `htazmbb.shop`

## 流水线

```
tempmail 建邮箱 → Chromium 注册 accounts.x.ai → SSO
    → Auth-Code+PKCE (referrer=grok-build)
    → POST cli-chat-proxy /v1/chat/completions model=grok-4.5
      （对齐 F:/opencode/edi/grok/check_alive.py：headers + messages 测活）
    → 仅 HTTP 200 且模型有回复才上传 grok2api pool=build
```

**403 / 439 / 429 / 401 等一律不上传。**

## 安装

```bash
cd F:\opencode\grokreg
uv sync
cp config.example.json config.json
# 编辑 proxy / grok2api_* 
```

需要本机 Chrome/Chromium。

## 配置

见 `config.example.json`。敏感项可用环境变量覆盖：

| Env | 配置键 |
|-----|--------|
| `PROXY_URL` | `proxy` |
| `GROK2API_REMOTE_BASE` | `grok2api_remote_base` |
| `GROK2API_APP_KEY` | `grok2api_remote_app_key` |
| `GROK2API_POOL` | `grok2api_pool_name` |
| `TEMPMAIL_BASE_URL` | `tempmail_base_url` |

## 命令

```bash
# 冒烟：创建临时邮箱
uv run python -m grokreg mail-smoke

# 注册 + 测活 + 上传
uv run python -m grokreg run --count 1

# 对已有账本只测活上传
uv run python -m grokreg probe-upload --accounts accounts.txt
```

账本格式：`email----password----sso`  
结果：`results.jsonl`

## GitHub Actions

Workflows：

- `register.yml`：全流程（需 Chromium + 建议 `PROXY_URL`）
- `probe_upload.yml`：仅测活+上传

Secrets：

- `PROXY_URL`（强烈建议）
- `GROK2API_REMOTE_BASE`
- `GROK2API_APP_KEY`
- `GROK2API_POOL`（可选，默认 Build）
- `TEMPMAIL_BASE_URL`（可选）

github-hosted 上 Turnstile 可能失败；生产建议 self-hosted + 住宅代理。

## 测试

```bash
uv run pytest -q
```

## 仓库拆分

- **注册机（本仓库）**：https://github.com/weige0831/grokreg  
- **调度前端**：https://github.com/weige0831/grok-scheduler  

## 合规

仅用于自动化流程研究、测试与个人学习。请遵守目标站服务条款与当地法律。
