# 可观测性指南

本文说明平台暴露的健康探针、运行指标与告警接入方式。

## 1. 健康探针

| 端点 | 类型 | 语义 | 返回 |
| --- | --- | --- | --- |
| `/health` | 兼容旧接口 | 静态存活信息 | 200 |
| `/health/live` | 存活（liveness） | 进程可响应即存活，**不检查外部依赖** | 200 |
| `/health/ready` | 就绪（readiness） | 关键依赖可用才就绪 | 200 / **503** |

**依赖分级**（见 `backend/app/core/observability.py`）：

- **数据库**：始终为关键依赖（业务数据唯一来源），不可达即就绪失败（503）。
- **Redis**：仅当 `MEMORY_SHORT_TERM_STORE=redis` 时为关键依赖；
  `auto` 模式下 Redis 不可用会降级为内存存储，不会导致就绪失败。

> 设计意图：存活探针不检查依赖，避免外部依赖抖动触发容器被反复重启；
> 就绪探针失败则由编排摘流，停止向该实例分发流量。

生产编排（`compose.prod.yml`）已为 backend 配置基于 `/health/ready` 的 `healthcheck`，
frontend 通过 `depends_on: backend: condition: service_healthy` 等待后端就绪后再启动。

## 2. 指标

`GET /metrics`（Prometheus 文本格式，`METRICS_ENABLED=false` 时返回 404）。

| 指标 | 类型 | 标签 | 说明 |
| --- | --- | --- | --- |
| `http_requests_total` | Counter | `method` `path` `status` | 请求总数 |
| `http_request_duration_seconds` | Histogram | `method` `path` | 请求耗时分布 |
| `http_requests_in_flight` | Gauge | — | 当前在途请求数 |
| `dependency_up` | Gauge | `dependency` | 外部依赖可用性（1/0） |

**标签基数控制**：`path` 取路由模板（如 `/api/v1/tasks/{task_id}`），
未匹配路由（404 等）归一为 `unmatched`；`/metrics` 自身不计入指标。

## 3. 抓取与告警

`compose.prod.yml` 已内置两个监控服务，配置见 `monitoring/`：

| 服务 | 镜像 | 作用 | 访问方式 |
| --- | --- | --- | --- |
| `prometheus` | `prom/prometheus:v2.55.1` | 抓取 `backend:8000/metrics`、评估告警规则 | `127.0.0.1:9090`（仅回环） |
| `alertmanager` | `prom/alertmanager:v0.28.1` | 告警聚合、抑制与路由 | `127.0.0.1:9093`（仅回环） |

- 抓取与告警转发均走 `edge_net`；后端同时接入 `data_net` 与 `edge_net`，
  因此**无需**为监控放开数据网。
- 管理 UI **只绑定回环地址**，不暴露公网；需要远程查看时请走跳板机或 SSH 隧道。
- Prometheus 未开启 `--web.enable-lifecycle`（避免未鉴权的配置热加载端点），
  改配置后需重启：`docker compose -f compose.prod.yml restart prometheus`。

### 告警规则（`monitoring/alerts.yml`）

| 告警 | 触发条件 | 级别 |
| --- | --- | --- |
| `BackendTargetDown` | 抓取目标连续 1 分钟不可达 | critical |
| `BackendDatabaseUnavailable` | `dependency_up{dependency="database"} == 0` | critical |
| `BackendRedisUnavailable` | `dependency_up{dependency="redis"} == 0` | warning |
| `BackendHighErrorRate` | 5xx 占比 > 5%（5 分钟窗口） | warning |
| `BackendHighLatencyP95` | P95 请求耗时 > 1s（5 分钟窗口） | warning |

### 通知渠道与凭据注入

`monitoring/alertmanager.yml` 的接收器 `ops-webhook` 是**真实投递**端点，
其 URL **不写入仓库**，而是运行时从文件读取：

| 项 | 值 |
| --- | --- |
| 宿主机文件 | `monitoring/secrets/webhook_url`（已被 `.gitignore` 排除） |
| 容器内路径 | `/etc/alertmanager/secrets/webhook_url` |
| 配置字段 | `webhook_configs[].url_file` |

创建步骤：

```bash
cp monitoring/secrets/webhook_url.example monitoring/secrets/webhook_url
vim monitoring/secrets/webhook_url        # 填入真实端点
docker compose -f compose.prod.yml restart alertmanager
```

**文件缺失会导致 alertmanager 启动失败**，这是刻意的 fail-fast ——
静默丢告警比起不来危险得多。

端点可填什么、以及"企业微信/钉钉群机器人不能直连"的原因，
见 `monitoring/secrets/README.md`。

> `compose.prod.yml` 的编排改动由 CI 的 `Validate production compose` 步骤校验
> （`docker compose config`），语法或变量插值错误不会拖到部署时才暴露。

### 告警演练

配置校验只能证明 YAML 合法，**不能证明告警发得出去**。
`monitoring/drill/alert_drill.py` 负责端到端演练：

```bash
# 本机全链路：本地接收器 + 用仓库配置启动 alertmanager + 断言投递到达
python monitoring/drill/alert_drill.py --mode local --alertmanager-bin /path/to/alertmanager

# 对已运行实例（生产容器）投递真实告警，验证真实渠道送达
python monitoring/drill/alert_drill.py --mode remote --alertmanager-url http://127.0.0.1:9093
python monitoring/drill/alert_drill.py --mode remote --resolve   # 再验恢复通知
```

产出与注意事项见 `monitoring/drill/README.md`。

## 4. 相关环境变量

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `METRICS_ENABLED` | `True` | 是否暴露 `/metrics` |
| `MEMORY_SHORT_TERM_STORE` | `auto` | 设为 `redis` 时 Redis 成为就绪的关键依赖 |
