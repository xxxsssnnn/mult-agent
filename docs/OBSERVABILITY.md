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

样例配置见 `monitoring/`：

- `prometheus.yml`：抓取 `backend:8000/metrics`；
- `alerts.yml`：可用性（目标不可达 / 依赖不可用）+ 性能（5xx 比例 / P95 延迟）告警。

接入步骤：

1. 在 `compose.prod.yml` 的 `edge_net` 上新增 `prometheus` 服务，挂载 `monitoring/` 到
   `/etc/prometheus/`；若需被外部访问，再按需限制入口。
2. 在 Prometheus / Alertmanager 侧配置告警路由（邮件、IM、工单等）。

## 4. 相关环境变量

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `METRICS_ENABLED` | `True` | 是否暴露 `/metrics` |
| `MEMORY_SHORT_TERM_STORE` | `auto` | 设为 `redis` 时 Redis 成为就绪的关键依赖 |
