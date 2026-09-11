# Alertmanager 通知凭据目录

本目录以**只读**方式挂载进 alertmanager 容器，对应容器内路径
`/etc/alertmanager/secrets`。

除本文件与 `*.example` 外，**本目录所有内容都被 `.gitignore` 排除**，
真实凭据不会进入仓库。

## 需要创建的文件

| 文件 | 内容 |
| --- | --- |
| `webhook_url` | 一行 URL，指向告警投递端点 |

创建方式：

```powershell
Copy-Item monitoring/secrets/webhook_url.example monitoring/secrets/webhook_url
notepad monitoring/secrets/webhook_url
```

```bash
cp monitoring/secrets/webhook_url.example monitoring/secrets/webhook_url
$EDITOR monitoring/secrets/webhook_url
```

然后重启：`docker compose -f compose.prod.yml restart alertmanager`

## 这个 URL 可以填什么

Alertmanager 会向该地址 POST 它自己的标准 JSON 告警负载
（`version` / `status` / `alerts[]` / `groupLabels` 等）。
因此端点需要能接受这个格式：

- **内部告警网关**：最直接，网关自行负责分发到 IM / 工单系统。
- **企业微信 / 钉钉 群机器人**：两者的群机器人只接受
  `{"msgtype":"text","text":{"content":"..."}}`，与 Alertmanager 的负载**格式不同**，
  中间需要一层适配器（例如 `prometheus-webhook-dingtalk`，
  它同时支持钉钉与企业微信）。**不能直接填群机器人地址**，否则接口会报参数错误。
- **PagerDuty**：建议改用 Alertmanager 原生的 `pagerduty_configs`，不要走 webhook。

## 为什么文件缺失会启动失败

Alertmanager 启动时就会读取 `webhook_url`，**文件不存在则进程直接退出**。

这是刻意设计：一个静默把告警丢掉的监控组件，比一个起不来的监控组件危险得多。
若确实要先跑起来，请显式把 `monitoring/alertmanager.yml` 的 `route.receiver`
指向 no-op 接收器，而不是留一个指向空气的 URL。

## 相关文件

- `../../monitoring/alertmanager.yml` —— 接收器与路由定义
- `../drill/README.md` —— 告警投递演练步骤
