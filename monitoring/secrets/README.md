# Alertmanager 通知凭据目录

本目录以**只读**方式挂载进 alertmanager 容器，对应容器内路径
`/etc/alertmanager/secrets`。

除本文件与 `*.example` 外，**本目录所有内容都被 `.gitignore` 排除**，
真实凭据不会进入仓库。

## 需要创建的文件

| 文件 | 内容 |
| --- | --- |
| `wechat_robot_url` | 一行 URL，企业微信群机器人的 Webhook 地址 |

创建方式：

```powershell
Copy-Item monitoring/secrets/wechat_robot_url.example monitoring/secrets/wechat_robot_url
notepad monitoring/secrets/wechat_robot_url
```

```bash
cp monitoring/secrets/wechat_robot_url.example monitoring/secrets/wechat_robot_url
$EDITOR monitoring/secrets/wechat_robot_url
```

然后重启：`docker compose -f compose.prod.yml restart alertmanager`

## 这个 URL 怎么来

企业微信 → 目标群 → 右上角「…」→ 群机器人 → 添加机器人 → 复制 Webhook 地址。

形如：

```text
https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=xxxxxxxx-xxxx-xxxx
```

`key` 就是凭据本身：拿到它的人可以往群里发消息。但它**只能发、不能读**，
泄露面可控，仍按凭据对待，只落在本目录。

## 为什么不需要中间适配器

群机器人只接受 `{"msgtype":...,"text|markdown":{"content":"..."}}`，与
Alertmanager 的默认负载格式不同。但 `webhook_configs.payload` 支持用 Go 模板
自定义负载，`monitoring/alertmanager.yml` 已经用它**直接生成**群机器人要求的
格式，因此不需要任何转发服务。

**这依赖 Alertmanager ≥ 0.32.0**（`payload` 与 `url` 模板化在该版本引入）。
编排锁定 **v0.34.0**。若降回 0.28.x，日志会报
`field payload not found in type config.plain` 并且**进程直接启动失败**。

> 0.34.0 同时修掉了「payload 字符串值被误当作 YAML 重新解析」的缺陷（#5304），
> 更低版本上含冒号结尾的告警文本可能被悄悄改写。

## 两个必须知道的行为

1. **`payload` 会整体替换默认负载。** 一旦自定义，`version` / `status` / `alerts`
   等字段不再发送，正文必须自己在模板里带全。所以这个端点**不再是通用 webhook**，
   不能直接换成内部告警网关（网关认不出格式）。
2. **模板渲染失败 = 告警发不出去。** 渲染错误被判定为 unrecoverable error，
   Alertmanager 会持续重试但永远不成功，日志里是
   `failed to render custom payload`。`amtool check-config` **查不出**这个问题
   （它不渲染模板），只有真发一次才会暴露 —— 所以改完必须跑演练：
   `python ../drill/alert_drill.py --mode local --alertmanager-bin <path>`。

## 群机器人的限制

| 限制 | 影响与既有对策 |
| --- | --- |
| 20 条/分钟 | 已用 `group_by` + `group_wait: 30s` / `group_interval: 5m` 聚合；超限时企业微信返回 errcode，可在 alertmanager 日志看到 |
| 只能发群、不能指定人 | 需要定向强提醒时改用下面的自建应用 |
| markdown 不支持 `@` | 通知正文不含 @ |

## 为什么文件缺失会导致启动失败

Alertmanager 启动时就会读取 `wechat_robot_url`，**文件不存在则进程直接退出**。

这是刻意设计：一个静默把告警丢掉的监控组件，比一个起不来的监控组件危险得多。
若确实要先跑起来，请显式把 `route.receiver` 指向 no-op 接收器，
而不是留一个指向空气的 URL。

## 可选：改用自建应用（可按部门/成员定向）

原生 `wechat_configs` 能指定接收人，但 `corp_id` / `agent_id` / `to_party`
三个**部署标识**没有文件注入形式，只能写进配置文件 —— 公开仓库会暴露组织标识，
这是默认不用它的原因。`api_secret` 可用 `api_secret_file` 注入
（0.28.1 上该字段不存在，本编排锁定的 0.34.0 已支持）。

## 相关文件

- `../../monitoring/alertmanager.yml` —— 接收器、payload 模板与路由定义
- `../drill/README.md` —— 告警投递演练步骤
