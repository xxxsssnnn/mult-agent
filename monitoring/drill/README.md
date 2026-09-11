# 告警投递演练

验证「告警产生 → Alertmanager 路由 → 真实投递端点」这条链路真的通。
入口是 `alert_drill.py`。

## 为什么需要单独演练

配置校验（`amtool check-config`）只能证明 YAML 结构合法，**不能证明告警发得出去**。

告警渠道现在用 `webhook_configs.payload` 渲染企业微信群机器人的负载，
这带来一类 `amtool` **查不出**的故障：payload 模板写错时，
Alertmanager 会把它判定为 unrecoverable error —— 持续重试但永远不成功，
日志里只有 `failed to render custom payload`。也就是**告警静默发不出去**，
而监控自己不会告警。所以改完配置必须真发一次。

## 依赖

- Python 3.8+
- `local` 模式额外需要 alertmanager 可执行文件，**版本要与容器镜像一致**：

```bash
curl -sSL -o am.tar.gz \
  https://github.com/prometheus/alertmanager/releases/download/v0.34.0/alertmanager-0.34.0.linux-amd64.tar.gz
tar xzf am.tar.gz
export ALERTMANAGER_BIN=$PWD/alertmanager-0.34.0.linux-amd64/alertmanager
```

Windows 取 `alertmanager-0.34.0.windows-amd64.zip`，解压后用 `--alertmanager-bin`
指向 `alertmanager.exe`。

> 版本必须与编排一致：`payload` 字段 0.32.0 才引入，用 0.28.x 的二进制跑演练
> 会因为 `field payload not found` 直接启动失败。

## local：本机全链路演练

```bash
python monitoring/drill/alert_drill.py --mode local --alertmanager-bin /path/to/alertmanager
```

它做了什么：

1. 在本机随机端口起一个 HTTP 接收器；
2. 复制 `monitoring/alertmanager.yml`，**只把 `url_file` 改成本机临时 secrets 路径**，
   其余（路由、分组、抑制规则、重复间隔、payload 模板）全部原样；
3. 用这份配置启动 alertmanager；
4. 向 `/api/v2/alerts` 投一条 `severity=critical` 的测试告警；
5. 断言收到的负载符合企业微信群机器人契约 —— `msgtype=markdown`、
   `markdown` 是**对象**而不是字符串、正文含 `DrillTestAlert`。

第 5 步是关键：`markdown` 退化成字符串就说明模板渲染出的不是合法 JSON，
群机器人会报参数错误，而这类问题在本地不校验就会被带到线上。

预期输出（耗时 30s 左右属正常，正好等于配置里的 `group_wait: 30s`）：

```text
[5/6] 已收到投递：msgtype=markdown 耗时=30.0s
      正文：**🔥 告警中** · DrillTestAlert | > 级别：critical ｜ 数量：1 |  | 告警链路演练 |  | [查看 Alertmanager](http://...)
演练通过：告警 -> 路由 -> 真实投递端点 全链路可用。
```

加 `--check-resolved` 可额外验证恢复通知（对应 `send_resolved: true`），
它会检查正文是否切换成「已恢复」：

```text
[6/6] 已收到恢复通知 耗时=300.2s
      正文：**✅ 已恢复** · DrillTestAlert | > 级别：critical ｜ 数量：1 | ...
```

**注意**：恢复通知受 `group_interval`（当前 5m）节流，需要
`--timeout 400` 之类更长的等待。

> Windows 控制台默认 GBK，emoji 会显示成 `?`，这是终端降级显示，
> 不影响实际负载内容（脚本已做容错，不会因此报错退出）。

## remote：对生产实例演练

`local` 模式证明不了「真实群里收到了」——那必须看接收端。生产演练：

```bash
# 1) 投递真实告警
python monitoring/drill/alert_drill.py --mode remote --alertmanager-url http://127.0.0.1:9093

# 2) 在企业微信群确认收到，记录到达时间

# 3) 验证恢复通知
python monitoring/drill/alert_drill.py --mode remote --resolve
```

`--alertmanager-url` 默认 `127.0.0.1:9093`。容器部署下该端口只绑回环，
所以需要先在宿主机侧经 SSH 隧道或跳板机访问。

演练记录建议至少包含：发起时间、群内到达时间（据此算延迟）、确认人，
以及未收到时的排查结论。

## 失败时怎么排查

| 现象 | 可能原因 |
| --- | --- |
| alertmanager 起不来，报 `field payload not found` | 二进制/镜像版本 < 0.32.0 |
| alertmanager 起不来，报读取 secrets 失败 | `monitoring/secrets/wechat_robot_url` 不存在（刻意 fail-fast） |
| 日志有 `failed to render custom payload` | payload 模板写错（字段名或语法），通知会一直重试但永不成功 |
| 找到不 alertmanager | 未设置 `--alertmanager-bin` 或 `ALERTMANAGER_BIN` |
| 超时未收到投递 | 端点地址错误、网络不通，或 `group_wait` 被调大 |
| 负载里 `markdown` 是字符串而非对象 | 模板渲染结果不是合法 JSON，检查引号与转义 |

## 清理

演练临时文件写在 `monitoring/drill/_work/`（已 gitignore），可随时删除。
每次运行会先清空再重建，所以不需要手工维护。
