# 告警投递演练

验证「告警产生 → Alertmanager 路由 → 真实投递端点」这条链路真的通。
入口是 `alert_drill.py`。

## 为什么需要单独演练

配置校验（`amtool check-config`）只能证明 YAML 结构合法，**不能证明告警发得出去**。
最常见的故障就是：配置看着没问题、Alertmanager 也在跑，但端点写错或网络不通，
告警被**静默丢弃**——而且没人会发现，因为监控自己不会告警。

## 依赖

- Python 3.8+
- `local` 模式额外需要 alertmanager 可执行文件，**版本要与容器镜像一致**：

```bash
curl -sSL -o am.tar.gz \
  https://github.com/prometheus/alertmanager/releases/download/v0.28.1/alertmanager-0.28.1.linux-amd64.tar.gz
tar xzf am.tar.gz
export ALERTMANAGER_BIN=$PWD/alertmanager-0.28.1.linux-amd64/alertmanager
```

Windows 取 `alertmanager-0.28.1.windows-amd64.zip`，解压后用 `--alertmanager-bin`
指向 `alertmanager.exe`。

## local：本机全链路演练

```bash
python monitoring/drill/alert_drill.py --mode local --alertmanager-bin /path/to/alertmanager
```

它做了什么：

1. 在本机随机端口起一个 HTTP 接收器；
2. 复制 `monitoring/alertmanager.yml`，**只把 `url_file` 改成本机临时 secrets 路径**，
   其余（路由、分组、抑制规则、重复间隔）全部原样；
3. 用这份配置启动 alertmanager；
4. 向 `/api/v2/alerts` 投一条 `severity=critical` 的测试告警；
5. 断言接收器收到了 `alertname=DrillTestAlert` 的投递。

预期输出（耗时 30s 左右属正常，正好等于配置里的 `group_wait: 30s`）：

```text
[5/6] 已收到投递：status=firing alerts=['DrillTestAlert'] receiver=ops-webhook 耗时=30.0s
演练通过：告警 -> 路由 -> 真实投递端点 全链路可用。
```

加 `--check-resolved` 可额外验证恢复通知（对应 `send_resolved: true`）。
**注意**：恢复通知受 `group_interval`（当前 5m）节流，需要
`--timeout 400` 之类更长的等待。

## remote：对生产实例演练

`local` 模式证明不了「真实渠道收到了」——那必须看接收端。生产演练：

```bash
# 1) 投递真实告警
python monitoring/drill/alert_drill.py --mode remote --alertmanager-url http://127.0.0.1:9093

# 2) 在接收端（企业微信 / 钉钉 / 邮件 / 网关）确认收到，记录到达时间

# 3) 验证恢复通知
python monitoring/drill/alert_drill.py --mode remote --resolve
```

`--alertmanager-url` 默认 `127.0.0.1:9093`。容器部署下该端口只绑回环，
所以需要先在宿主机侧经 SSH 隧道或跳板机访问。

演练记录建议至少包含：发起时间、接收端到达时间（据此算延迟）、接收人，
以及未收到时的排查结论。

## 失败时怎么排查

| 现象 | 可能原因 |
| --- | --- |
| alertmanager 起不来 | `monitoring/secrets/webhook_url` 不存在（刻意 fail-fast） |
| 找不到 alertmanager | 未设置 `--alertmanager-bin` 或 `ALERTMANAGER_BIN` |
| 超时未收到投递 | 端点地址错误、网络不通，或 `group_wait` 被调大 |
| 接收端报参数错误 | 直接填了企业微信 / 钉钉**群机器人**地址而缺少适配器，见 `../secrets/README.md` |

## 清理

演练临时文件写在 `monitoring/drill/_work/`（已 gitignore），可随时删除。
