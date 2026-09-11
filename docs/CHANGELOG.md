# 修改记录（CHANGELOG）

> 本文件按时间倒序记录每一次代码修改：**改了什么**、**为什么这么改**、**解决了什么问题**。
> 每条记录对应一次 git 提交，便于回溯与审计。

---

## 2026-09-11 前端安全债批次 3：@typescript-eslint 升级 + 传递依赖 overrides，前端基线 14 → 8 条

**提交**：本次提交（安全债批次 3）

**改了什么**：

- `frontend/package.json`：
  - `@typescript-eslint/eslint-plugin`、`@typescript-eslint/parser` 6.21.0 → **7.18.0**
  - 新增 `overrides`：把 `@ant-design/pro-layout` 下的 `path-to-regexp` 强制到 `^8.4.2`
- `frontend/package-lock.json`：`minimatch` 9.0.3→9.0.9、`js-yaml` 4.3.1→4.3.2、
  `path-to-regexp` 8.2.0→8.4.2，`@typescript-eslint/*` 全家 6.21.0→7.18.0
- `security/audit-ci.json`：allowlist 14 条 → **8 条**（删掉已解除的 6 条）
- `docs/SECURITY_DEBT.md`：批次 3 标完成，新增第 5.1 节记录"上游精确钉死"这类坑

**为什么这么改**：按台账批次 3 推进，目标是清掉 6 条 advisory。

**三个包都不是直接依赖，难点各不相同**：

1. `minimatch`（3 条）：由 `@typescript-eslint/typescript-estree` 引入。
   6.21.0 把它**精确**钉成 `9.0.3`（不是 `^9.0.3`），所以只能升 `@typescript-eslint`。
   升到 7.18.0 后它声明 `minimatch: ^9.0.4`，解析到 9.0.9，三条 ReDoS 一并解除。
2. `js-yaml`（1 条）：由 `eslint` 以 `^4.1.0` 引入。**本地 `node_modules` 里其实已是
   4.3.2（修复版），但锁文件里还是 4.3.1** —— 两者漂移。`npm audit` 以锁文件为准，
   所以一直报这条；而 CI 用 `npm ci` 严格按锁安装，拿到的正是 4.3.1。
   仅凭"本地装的是新的"不能判定已修复，必须以锁文件 / `npm audit` 为准。
3. `path-to-regexp`（2 条）：由 `@ant-design/pro-layout` 引入，上游同样**精确**钉成
   `8.2.0`。**查过 registry：`@ant-design/pro-layout` 的 `latest` 仍是 7.22.7，
   钉的还是 8.2.0 —— 上游根本没发修复版**，升级父包无效。
   对精确钉死的传递依赖，`overrides` 是唯一可行的解除方式。
   作用域刻意收窄到 `@ant-design/pro-layout` 这一个父包，不影响其他潜在消费方。

**关键取舍：没有升到 @typescript-eslint 8.x**

`latest` 已是 8.70.0，但升它会把 `eslint-visitor-keys` 拉到 5.x，
而 5.x 的 `engines` 要求 node `^20.19.0 || ^22.13.0 || >=24`，
**与本项目 CI 的 node 18 不兼容**。7.18.0 是最后一个 7.x，
`engines` 为 `^18.18.0 || >=20.0.0`，与 CI 一致，且同样能解除 minimatch。
留在 7.x 是有意为之：本批次的目标是清掉这 6 条具体 advisory，
而不是顺带改掉 Node 版本矩阵；升 8.x 应作为独立批次、连带动 CI 的 Node 版本。

**怎么验证的**：

- `tsc --noEmit` → exit 0
- `npm run lint`（`--max-warnings 0`）→ exit 0（7.x 的 recommended 规则集未产生新告警）
- `npm run test` → **3 个文件 / 8 个用例全过**（13.02s）
- `npm run build`（`tsc && vite build`）→ 成功（13.52s，仅原有的 chunk 体积提示）
- `npm audit` → **17 项 → 7 项**，剩下 7 项恰好是批次 4 的范围
- 按 CI 方式跑 `audit-ci --high`（用收缩后的 8 条白名单）→ **Passed，exit 0**；
  输出里被豁免的只剩 `GHSA-5xrq-8626-4rwp`、`GHSA-fx2h-pf6j-xcff` 两条
- **反向对照**：把仍然存在的 `vite` high 从白名单里拿掉再跑 `audit-ci` →
  `Failed security audit due to high vulnerabilities`，exit 1。
  这一步证明门禁没有被"白名单化"架空，仍能拦截未登记的高危。

**仍未验证**：完整门禁只在 node 20.11 上跑过，没有在 CI 实际的 node 18 上本地复现；
依据是 7.18.0 的 `engines` 明确覆盖 node 18.18+，且未引入要求更高 Node 的传递依赖
（这正是没有选 8.x 的原因）。

---

## 2026-09-11 后端安全债批次 2：FastAPI/starlette 升级，基线 37 → 29 项

**提交**：本次提交（安全债批次 2）

**改了什么**：

- `backend/requirements.txt`：`fastapi` 0.109.0 → **0.141.1**；`pydantic` 2.5.3 → 2.13.5
  （被动上调：0.141.1 要求 `pydantic>=2.9`）；补注释说明 starlette 由锁文件钉住。
- `backend/requirements.lock`：`fastapi` 0.141.1、`starlette` 1.6.0、`pydantic` 2.13.5、
  `pydantic_core` 2.46.5，新增 `typing-inspection==0.4.4`（pydantic 2.13 的新依赖）。
- `security/pip-audit-baseline.txt`：删除 C 组 8 个条目（**37 项 → 29 项**）。
- `docs/SECURITY_DEBT.md`：批次 2 标记完成，更新计数。

**为什么这么改**：按台账批次 2 推进。这 8 项里 `starlette` 最高要求 fix 1.3.1，
而 FastAPI 0.109 把 starlette 钉在 `<0.36` —— 单独升 starlette 不可行，
必须整体跨到 starlette 1.x。这是台账里把它列为一组的原因。

**关键取舍：没有用 `pip freeze` 重生成锁文件**

`requirements.lock` 的头注释写明它来自「venv 的 `pip freeze`」。
但当前 venv 里额外装了 `pytest` / `bandit` / `pip-audit` 及其一串传递依赖
（`cyclonedx-python-lib`、`license-expression`、`html5lib`、`CacheControl` …）。
直接按注释重生成，会把这 20 多个**测试期工具**写进运行时锁文件，污染生产依赖面。
因此改为**只手工改必要的 5 行**，最终 `git diff` 恰好 5 处（4 处改版本 + 1 处新增）。

> 附带发现：PowerShell 的 `Sort-Object` 是文化相关排序（会忽略 `-` 等标点），
> 与 `pip freeze` 的排序不一致，重生成还会带来大量顺序扰动。手工改反而更干净。

**怎么验证的**：

- 变更集先经 `pip install --dry-run` 确认只影响 5 个包，范围可控后才动手。
- 全量回归门禁 **27/27 通过（152.9s）**。
- `pip check` → `No broken requirements found.`
  （`packaging<24` / `build<1.5` / `chromadb` 那组脆弱三方约束未被打破）。
- `bandit -r backend/app -ll` → 0 medium/high，与台账记录一致。
- `pip-audit` 带**去掉这 8 项后的基线**运行 → `No known vulnerabilities found, 29 ignored`，
  退出码 0。这一步是必要的：只有「不给这些 ID 豁免」时仍通过，
  才证明它们真被修掉，而不是被基线掩盖。
- 不带任何豁免的全量扫描 → `29 known vulnerabilities in 9 packages`，
  分组与基线完全吻合（chromadb 4 + ecdsa 1 + langchain 全家桶 24）。
- 顺带确认 `requirements-dev.txt` 只有 pytest/pip-audit/bandit，无框架版本约束，不会冲突。

**顺带发现的一个运维风险**：本次扫描发现 chromadb 的 advisory ID 已从
`GHSA-2wm9-hf6c-p5cr` 等变为 `PYSEC-2026-3813/3814/3815`（同一批漏洞换了标识）。
实测现有 GHSA 豁免仍能通过别名覆盖它们、CI 不会因此红灯，
但这说明**基线存在"ID 漂移导致失配"的风险**：一旦上游改用不互为别名的 ID，
基线就会静默失效并让 CI 红灯。已记入台账的第 9 节复核事项。

**仍未验证**：运行期行为只覆盖到回归门禁的深度。未在真实
PostgreSQL/Redis/Chroma 环境下跑集成测试（本机无 Docker），也未做性能压测。

---

## 2026-09-11 告警改投企业微信群机器人 + Alertmanager 升级至 0.34.0

**提交**：本次提交（企业微信渠道）

**改了什么**：

- `monitoring/alertmanager.yml`：接收器从通用 `ops-webhook` 改为企业微信专用的
  `ops-wechat`，用 `webhook_configs.payload` 的 Go 模板直接渲染群机器人负载。
- `monitoring/secrets/`：凭据文件按新契约改名 `webhook_url` → `wechat_robot_url`，
  示例文件与 README 同步重写。
- `compose.prod.yml` / `ci.yml`：Alertmanager 镜像 `v0.28.1` → **`v0.34.0`**。
- `monitoring/drill/alert_drill.py`：断言改为企业微信群机器人负载契约
  （`msgtype=markdown`、`markdown` 必须是对象、正文含告警名/恢复字样），
  并修掉 Windows GBK 控制台下 emoji 导致演练崩溃的问题。
- `docs/OBSERVABILITY.md`、`monitoring/drill/README.md` 同步更新。

**为什么改**：

1. 上一轮把接收器接到了通用 webhook，但你们选定的渠道是企业微信。企业微信/钉钉的
   群机器人只接受 `{"msgtype":...,"markdown":{"content":"..."}}`，与 Alertmanager
   默认负载格式不同。
2. 我上一轮在 `secrets/README.md` 里写过「群机器人必须加一层适配器」——
   **这个结论是错的**。`webhook_configs.payload` 支持自定义负载，
   Alertmanager 自己就能渲染出机器人格式，不需要额外服务。
   也正因如此，避免了一个更糟的方案：把适配器放进**被监控的本服务**里，
   那会造成「后端挂了 → 告警经后端转发 → 转发不出去」的循环依赖，
   恰好丢掉最该收到的告警。

**关键发现：编排锁定的 0.28.1 太旧，不升级就没有干净路径**

最初直接用 `payload` 时实测报错：

```text
field payload not found in type config.plain
```

用官方二进制逐项实测后确认的能力差异：

| 能力 | 0.28.1（原锁定） | 0.34.0（新锁定） |
| --- | --- | --- |
| `webhook_configs.payload` | 不支持 | 支持 |
| `wechat_configs.api_secret_file` | 不支持 | 支持 |
| `wechat_configs.api_url` | 未验证 | 支持 |

`payload` 与 `url` 模板化是 **0.32.0（2026-04-08）** 引入的；0.28.1 是 2025-03 的版本。
0.34.0 另外修掉了「payload 字符串值被误当作 YAML 重新解析」的缺陷（#5304）。

也就是说，留在 0.28.1 只有两个选择：给群机器人配一层适配器服务，
或者用原生自建应用把 `api_secret` 明文写进仓库配置。
升级是唯一同时满足「无额外服务、无内联凭据、无组织标识入库」的路径。

**设计取舍：为什么用群机器人而不是原生自建应用**

原生 `wechat_configs` 能按部门/成员定向发送，但 `corp_id` / `agent_id` / `to_party`
三个**部署标识**没有文件注入形式，只能写进配置文件。这是公开仓库，
把组织标识提交进去不合适。群机器人只需要**一个 URL**，而 URL 恰好支持 `url_file`
注入 —— 于是仓库里做到了零标识、零内联凭据。

代价：群机器人限速 20 条/分钟，且不能指定接收人。已用
`group_by` + `group_wait: 30s` / `group_interval: 5m` 聚合缓解；
限制与备选方案写进了 `secrets/README.md`。

**怎么验证的**：

- `amtool check-config`（0.34.0）→ `SUCCESS`，并确认现有配置在 0.34.0 下没有不兼容项。
- 演练实跑，用的是仓库里的 `alertmanager.yml`（唯一改动：`url_file` 一行）：
  - 投递：`msgtype=markdown 耗时=30.0s`，正文
    `**🔥 告警中** · DrillTestAlert | > 级别：critical ｜ 数量：1 | 告警链路演练 | [查看 Alertmanager](...)`
  - 恢复：`耗时=300.2s`，正文切换为 `**✅ 已恢复** · DrillTestAlert`
  - 30.0s / 300.2s 分别等于配置里的 `group_wait` 与 `group_interval`
- 演练断言的是**企业微信群机器人负载契约**而非「有东西发出来」：
  `msgtype=markdown`、`markdown` 必须是对象而非字符串。
  模板渲染出非法 JSON 时 Alertmanager 会把该值退化成字符串，群机器人随后报参数错误 ——
  `amtool` 查不出这类问题，只有真发一次才能暴露。

**仍未验证（未声称已完成）**：

- 企业微信群的**真实送达**：需要你们创建群机器人并写入
  `monitoring/secrets/wechat_robot_url`，再跑一次 `alert_drill.py --mode remote`。
- 容器内行为（本机无 Docker）：secrets 挂载，以及 0.34.0 读取 0.28.1 遗留
  数据目录时的行为。若之前已在跑 0.28.1，静默规则（silences）可能需要重建。

---

## 2026-09-11 告警接入真实投递渠道 + 端到端演练 + 官方工具校验

**提交**：本次提交（告警渠道与演练）

**改了什么**：

- `monitoring/alertmanager.yml`：把 no-op `default` 接收器换成**真实投递**的
  `ops-webhook`；端点经 `url_file` 从文件注入；`send_resolved: true` 支持恢复通知。
- 新增 `monitoring/secrets/`：`webhook_url`（运行时凭据，已 gitignore）、
  `webhook_url.example`、`README.md`（端点类型说明与 fail-fast 原因）。
- `compose.prod.yml`：alertmanager 增加挂载
  `./monitoring/secrets:/etc/alertmanager/secrets:ro`。
- 新增 `monitoring/drill/alert_drill.py` + `README.md`：端到端告警投递演练
  （`--mode local` 本机全链路 / `--mode remote` 对生产实例投递）。
- `.gitignore`：排除 `monitoring/secrets/*`（保留 README 与 example）与演练临时目录。
- `docs/OBSERVABILITY.md`：重写通知渠道与演练章节。

**为什么这么改**：P0 问题"告警还不会真正通知人"——此前接收器是 no-op，
告警链路在**最后一跳断掉**。另外我上一轮明确记下"`promtool` / `amtool` 校验未做"，
规则里的 PromQL 到底能不能触发一直缺证据。

**设计取舍：为什么用 webhook 而不是原生企业微信**

- 企业微信原生 `wechat_configs` 需要 `corp_id` + `agent_id` + `to_party` + `api_secret`
  四个**部署相关**取值；其中只有 `api_secret` 支持 `api_secret_file` 文件注入，
  其余三个必须写进配置文件。这是公开仓库，把组织标识提交进去不合适。
- `webhook_configs` 只需要**一个** URL，且支持 `url_file`，
  于是仓库里可以做到**零部署标识、零凭据**。
- 代价：企业微信/钉钉**群机器人**只接受 `{"msgtype":"text","text":{"content":...}}`，
  与 Alertmanager 的负载格式不同，中间需要一层适配器
  （如 `prometheus-webhook-dingtalk`）。这一点已写进 `secrets/README.md`，
  避免有人把群机器人地址直接填进去、然后以为告警通了。

**怎么验证的（这次是真跑过，不再是"待补做"）**：

- 用与编排**锁定版本一致**的官方二进制在本机实跑：
  - `promtool check rules monitoring/alerts.yml` → `SUCCESS: 5 rules found`
  - `promtool check config`（`prometheus.yml`，仅把 `rule_files` 改成本地路径）→
    `SUCCESS: 1 rule files found`、`is valid prometheus config file syntax`
  - `amtool check-config monitoring/alertmanager.yml` → `SUCCESS`（1 receiver、1 inhibit rule）
- `alert_drill.py --mode local` 实跑通过，**用的就是仓库里的 `alertmanager.yml`**
  （唯一改动：`url_file` 指向本机临时 secrets 目录）：
  - 投递告警：`status=firing alerts=['DrillTestAlert'] receiver=ops-webhook 耗时=30.0s`
  - 恢复通知：`status=resolved 耗时=300.2s`
  - 30.0s 与 300.2s 分别精确等于配置里的 `group_wait: 30s` 和
    `group_interval: 5m`，说明生效的是真实配置而非测试替身。

**仍未验证（未声称已完成）**：

- 真实企业微信/钉钉/邮件端点的**最终送达**，需要你们的凭据与端点。
  `--mode remote` 就是为此准备的，届时在接收端确认即可。
- 容器内的运行行为（本机无 Docker）：`webhook_url` 挂载、alertmanager 启动时机、
  与 Prometheus 的联动，都还没实机跑过。

---

## 2026-09-11 监控组件接入生产编排：Prometheus + Alertmanager

**提交**：本次提交（监控接入）

**改了什么**：

- `compose.prod.yml` 新增两个监控服务（均只绑定回环、`no-new-privileges`、显式资源限额）：
  - `prometheus`（`prom/prometheus:v2.55.1`）：抓取 `backend:8000/metrics`，
    管理 UI → `127.0.0.1:9090`；
  - `alertmanager`（`prom/alertmanager:v0.28.1`）：告警聚合 / 抑制 / 路由，
    管理 UI → `127.0.0.1:9093`；
  - 新增命名卷 `prometheus_data` / `alertmanager_data`。
- `monitoring/prometheus.yml`：新增 `alerting` 段，指向 `alertmanager:9093`。
- 新增 `monitoring/alertmanager.yml`：默认 no-op 接收器 + 分组/重复间隔配置 +
  抑制规则（抓取目标不可达时抑制其派生告警，避免告警风暴）。
- `.github/workflows/ci.yml`：Docker Build 任务新增 `Validate production compose` 步骤，
  用 `docker compose config` 校验 `compose.prod.yml`。

**为什么这么改**：上一步只解决了"应用能产出指标"，但没有任何组件负责采集、存储、
评估规则与发送告警，**监控链路是断的**。同时 `compose.prod.yml` 此前完全没有 CI 校验，
编排错误只能等到部署时才暴露。

**设计取舍**：

- 监控组件接入 `edge_net`：后端同时位于 `data_net` 与 `edge_net`，因此**无需**为监控放开
  internal 数据网，数据面隔离不被削弱。
- 管理 UI 只绑定 `127.0.0.1`，沿用 backend 的既有做法，不新增公网入口。
- Prometheus **不依赖** Alertmanager 即可启动 —— 告警通道故障不应中断指标采集。
- 不开启 `--web.enable-lifecycle`：避免出现未鉴权的配置热加载端点，改配置走显式重启。
- Alertmanager 默认接收器为 no-op：避免在未配置渠道时把告警发往未知去处。

**怎么保证没引入回归（含验证边界）**：

- **本机无 Docker**，因此 `docker compose config` 与真实启动**未能在本地执行**。
  本地已完成的可验证项：对 `compose.prod.yml`、`monitoring/*.yml`、`ci.yml` 做
  YAML 解析 + 结构断言（服务与卷齐全、端口仅回环、健康检查与告警目标指向正确）。
- 新增的 CI 步骤会在 GitHub Runner 上用 `docker compose config` 补上编排校验；
  该步骤刻意复用 `.env.prod.example` 的**既有占位符**（`cp .env.prod.example .env`），
  **不引入任何新的凭据字面量**，因此 `.gitleaks.toml` 无需新增豁免。
- 告警规则的语义正确性（`promtool` / `amtool` 校验）仍待有 Docker 的环境补做 ——
  这一点已明确记录，未声称已验证。

---

## 2026-09-10 可观测性补齐：存活/就绪探针 + Prometheus 指标

**提交**：本次提交（可观测性补齐）

**改了什么**：

- 新增 `backend/app/core/observability.py`：
  - `PrometheusMiddleware`（纯 ASGI 中间件）：采集 `http_requests_total`、
    `http_request_duration_seconds`、`http_requests_in_flight`；
  - `collect_readiness()`：探测数据库（`SELECT 1`）与 Redis（`PING`）连通性，
    导出 `dependency_up{dependency=...}` gauge；
  - `path` 标签取**路由模板**（如 `/api/v1/tasks/{task_id}`），未匹配路由归一为
    `unmatched`，避免 UUID 类路径把标签基数打爆。
- `app/main.py`：新增三个端点
  - `/health/live` 存活探针（不检查外部依赖）；
  - `/health/ready` 就绪探针（关键依赖不可用返回 **503**）；
  - `/metrics` Prometheus 出口（`METRICS_ENABLED=false` 时返回 404）。
- `app/core/config.py`：新增 `METRICS_ENABLED`（默认开启）。
- `compose.prod.yml`：backend 增加基于 `/health/ready` 的 `healthcheck`；
  frontend 的 `depends_on` 改为 `condition: service_healthy`（后端未就绪不启动前端）。
- 依赖：新增 `prometheus-client==0.23.1`（纯 Python，无传递依赖），
  已同步 `requirements.txt` 与 `requirements.lock`。
- 新增 `monitoring/`（Prometheus 抓取配置 + 告警规则样例）与 `docs/OBSERVABILITY.md`。

**为什么这么改**：评估报告指出"运维/可观测性不足"——此前只有静态 `/health`，
缺少存活/就绪区分与运行指标，编排无法据此摘流或重启，也无从观测延迟与错误率。

**设计取舍**：

- 就绪探针**分级判定**：数据库始终为关键依赖；Redis **仅**在
  `MEMORY_SHORT_TERM_STORE=redis` 时视为关键——因为 `auto` 模式下 Redis 不可用会
  降级为内存存储，若把它一律算作关键会导致误摘流。
- 存活探针**不检查外部依赖**，避免依赖抖动触发容器被反复重启。

**怎么保证没引入回归**：

- `pip check` 无破损依赖；`bandit -r backend/app -ll` → `No issues identified`。
- 按 CI 的方式展开基线豁免跑 `pip-audit` → `No known vulnerabilities found, 37 ignored`，
  退出码 0（基线条目仍为 37，新增依赖未带来漏洞）。
- 完整门禁 **27/27 通过（156.1s）**；pytest 套件 14 项全过（含新增 6 项可观测性用例）。

---

## 2026-09-10 依赖基线开始收缩：低风险组 12 项清零（49 → 37）

**提交**：本次提交（低风险依赖升级）

**改了什么**：按台账第 6 节的批次计划完成**第一批次（低风险组）**的依赖升级，
并同步收缩基线：

| 包 | 升级前 | 升级后 | 消除漏洞 |
| --- | --- | --- | --- |
| `python-multipart` | 0.0.6 | **0.0.32** | 8 项 |
| `python-jose[cryptography]` | 3.3.0 | **3.5.0** | 3 项 |
| `python-dotenv` | 1.0.0 | **1.2.3** | 1 项 |

后端基线从 **49 项 / 14 个包** 降到 **37 项 / 12 个包**；
`security/pip-audit-baseline.txt` 的 D 组 12 条豁免已按"只允许缩小"的规则删除，
台账（`docs/SECURITY_DEBT.md`）的分组表与批次计划同步更新。

**为什么这么改**：这是"先豁免、后升级"策略的第二阶段。基线只是让 CI 先恢复工作，
企业交付前必须持续缩小，尤其是**运行时**依赖 —— 这三条都直接服务于请求解析与令牌签名，
不能长期豁免。

**怎么保证没引入回归**：

- **改锁文件前先证明等价性**：把 venv 的 `pip freeze` 与 `requirements.lock` 做
  包名/版本双向比对，结论是"版本不同**恰好 3 个**、仅在 lock **为空**、
  仅在 freeze 的 24 个全是 dev 工具（pytest / bandit / pip-audit / coverage …，
  本就不该进锁文件）"。因此改这 3 行与"重新生成锁文件"完全等价，没有引入漂移。
- `pip check`：无破损依赖（`packaging` / `build` 的既有约束未被触碰）。
- 完整门禁 **27/27 通过（165.2s）**，其中 `test_auth_closure.py` 的 23 项鉴权断言
  覆盖了 jwt 升级的兼容性。
- 基线收缩后按 CI 的方式展开豁免参数跑 `pip-audit` → `No known vulnerabilities found,
  37 ignored`，退出码 0；并校验基线文件提取出的 37 个 token 全部形如
  `PYSEC-*` / `GHSA-*`（无格式污染）。

**踩坑留档**：台账里原先写的"`ecdsa` 可通过改用 `python-jose[cryptography]` 绕开"
是**错的**，已修正。实测 `python-jose[cryptography] 3.5.0` 仍硬依赖 `ecdsa!=0.15`
（dry-run 明确显示 `Requirement already satisfied: ecdsa!=0.15`），而锁文件里本来就
同时装着 `cryptography`。所以 `ecdsa 0.19.2` 那 1 项**不能靠换 jose 后端解决**，
只能跟踪上游 —— 如果当初按这个错误判断去"解决"，会在最后验收时才发现漏洞仍在。

---

## 2026-09-10 CI 第二轮：gitleaks 误报修复

**提交**：本次提交（gitleaks 误报修复）

**改了什么**：CI 第二个 run（`de501eb`）的失败点**不是依赖扫描，而是 gitleaks**。
本地按 CI 的实际工具版本复现后，修掉 13 条误报，并把扫描范围拉回可控：

- **文档占位符统一为 `<YOUR_TOKEN>`**（12 处）：`QUICKSTART.md`、
  `WORKFLOW_IMPLEMENTATION.md`、`docs/RAG_USAGE_GUIDE.md`、
  `docs/RAG_IMPLEMENTATION_SUMMARY.md`、`docs/WORKFLOW_V2_COMPLETION_REPORT.md`
  中的 `Authorization: Bearer YOUR_TOKEN` → `Bearer <YOUR_TOKEN>`，
  与仓库里已有的 `Bearer <token>` 写法保持一致。
- **测试夹具显式放行**：`backend/tests/pytest_suite/conftest.py` 的固定假 `SECRET_KEY`
  保留原值（测试需要足够熵的值），加 gitleaks 官方行内放行 `# gitleaks:allow`。
- **新增 `.gitleaks.toml`**：继承官方全部规则（`[extend] useDefault = true`），
  只加 3 条**按行匹配**的字面占位符 allowlist。作用是兜住**不可变的历史提交** ——
  旧 blob 里仍有 `Bearer YOUR_TOKEN`，改文件消不掉它们。
- **security 任务补 `fetch-depth: 0`**：原先的浅克隆让 gitleaks-action
  **退化为"整个项目快照"扫描**，而不是按 push 范围扫描。

**为什么这么改**：这次失败的成因有两个，都不是"代码里有真凭据"：

1. **规则集版本决定结论**。本机原有的 gitleaks 8.18.4 扫全量历史是 **0 命中**，
   而 gitleaks-action 实际用的 **8.30.1 报 13 条** —— `curl-auth-header`
   是较新版本才加入的规则。"本地扫过没问题"在这里是个假阴性。
2. **浅克隆改变扫描范围**。实测本次推送范围（`a71235c..de501eb`）本身是 **0 命中**，
   但 CI 仍报 leaks，说明它扫的不是这个范围，而是整个项目快照。

**解决了什么问题**：CI 安全任务的红灯从"必然失败"收敛为"仅剩已定位并留档的误报"。
实证（均用 8.30.1，且**不显式指定配置**，以复现 CI 的自动探测行为）：

- 修复前：全量历史 `exit 1`，13 条命中
- 修复后：全量历史 `exit 0`、工作树 `exit 0`，非 vendor 命中 **0**
- **反向对照**（证明 allowlist 没把扫描器变成摆设）：临时目录放入真实形态的
  高熵密钥 → 仍被 `generic-api-key` 捕获；同时放入 `Bearer <YOUR_TOKEN>` 与
  `sk-your-key-here` → 0 命中
- 完整门禁 `27/27 通过`（150.7s）

**已核实的 CI 现状**（通过公开的 Actions 页面核实；本机无 GitHub CLI，公共 API 被限流）：

| Run | Commit | 后端测试 | 前端检查 | 安全扫描 | Docker 构建 |
| --- | --- | --- | --- | --- | --- |
| #1 | `a71235c` | 通过 | 通过 | **失败**（pip-audit，当时依赖基线尚未落地） | 通过 |
| #2 | `de501eb` | 通过 | 通过 | **失败**（gitleaks 误报） | **通过** |

两个结论：依赖基线豁免已经放行（否则 gitleaks 之前的步骤就会失败）；
**两个镜像都能成功构建**，"Docker Build 未验证"这个未知项就此消掉。

---

## 2026-09-10 CI 安全门禁落地：依赖漏洞基线与安全债台账

**提交**：本次提交（CI 安全门禁基线豁免）

**改了什么**：

- **先复查：CI 安全任务首次真实运行必然红灯**。在推送前用 CI 里完全相同的命令本地复现，
  拿到的是硬退出码：
  - `pip-audit -r backend/requirements.lock` → **49 项 / 14 个包，exit 1**
  - `npm audit --audit-level=high` → **17 项 / 14 条 advisory（含 2 critical），exit 1**

- **策略：先豁免、后升级**。把历史欠账做成显式、可审计、可收敛的基线，而不是放宽门禁
  （把 job 改成 `continue-on-error` 会让门禁彻底失去拦截力，那是不可接受的）：
  - `security/pip-audit-baseline.txt`：49 个漏洞 ID 的基线清单，按"A 无修复版本 /
    B langchain 迁移 / C FastAPI+starlette 批次 / D 低风险可升"分组标注，并写明维护规则
  - `security/audit-ci.json`：前端 14 条 advisory 的 allowlist（GHSA 编码）
  - `docs/SECURITY_DEBT.md`：安全债台账 —— 基线明细、实际暴露面判断、五批次升级计划、
    Bandit 豁免留档、复核节奏与退出条件（**基线清空即回到纯门禁**）

- **CI 实现**：
  - 后端：`pip-audit --ignore-vuln`，参数由基线文件展开。**未登记的漏洞仍会红灯**
  - 前端：改用 `audit-ci@7.1.0`（`npm audit` 不支持按 advisory 排除），
    `--high` 与原先 `npm audit --audit-level=high` 语义一致
  - 安全任务的工具改为从 `backend/requirements-dev.txt` 安装，与其它任务一样锁定版本
    （原先是不固定版本的 `pip install pip-audit` / `pip install bandit`）

**为什么这么改**：远程 CI 一旦真实运行，安全任务就必然红灯。关键判断是：**这道门禁无法
只靠升级变绿** —— `chromadb 1.5.9`（4 项）与 `ecdsa 0.19.2`（1 项）上游**没有修复版本**，
无论怎么升都会持续命中，所以豁免机制不可避免。既然必须有基线，就该把基线做成显式资产。

前端同理：`npm audit fix` 实测只改动 4 个包、high/critical **一条都没解决**，
因为修复都要破坏性大版本（`react-router` 6→7、`vite` 5→8、`@typescript-eslint` 6→7），
不是 `audit fix` 能自动完成的。

**解决了什么问题**：CI 安全任务从"必然红灯"变为"**对新增漏洞红灯、对已登记欠账放行**"，
试点不被历史依赖债阻塞。三个实证（不依赖 GitHub CI）：

- 后端：在 WSL 中**逐字复现** CI 的 bash 管道 —— 完整基线 `exit 0`（`49 ignored`）；
  从基线删掉 1 个 ID 后 `exit 1`，且精确报出被删的那个 ID（证明门禁未被废掉）
- 前端：`audit-ci` 用完整 allowlist → `exit 0`（`Passed npm security audit.`）；
  删掉 1 条 GHSA → `exit 1`
- `.github/workflows/ci.yml` 经 YAML 解析校验，四个 job 与安全任务全部步骤齐全

**踩坑留档**：`audit-ci` v7 起 allowlist **只接受 GHSA 标识**，传数字 advisory ID 会直接抛
`Unsupported number as allowlist` 而失败（最初按数字写，实证时才暴露）。

---

## 2026-09-10 上线阻断项修复：生产 Celery 启动路径 + Bandit 扫描全绿

**提交**：`a71235c`（fix: 修正 Celery 启动路径；非安全用途的 SHA-1 统一改为 SHA-256）

**改了什么**：

- **生产 Celery 启动路径修正**：worker / beat 启动命令由 `-A app.celery_app` 改为
  `-A app.core.celery_app`。实际模块是 `backend/app/core/celery_app.py`，`app.celery_app`
  并不存在 —— 后果是生产 API 可正常启动，但**异步 Worker 与定时 Beat 启动即
  `ModuleNotFoundError`**（任务全部投不出去）。覆盖 `compose.prod.yml`、`docker-compose.yml`，
  以及 3 处会误导后来者的文档字符串 / 注释（`app/core/celery_app.py`、`app/tasks/memory_tasks.py`、
  `tests/test_celery_registration.py`）。
  实证（不依赖 Docker）：用 `celery.app.utils.find_app` 直接解析启动路径 ——
  `app.core.celery_app` 成功（main=`multi_agent`、include=`['app.tasks.memory_tasks']`、9 个任务），
  `app.celery_app` 抛 `ModuleNotFoundError`。

- **Bandit 高危 / 中危项清零**：`bandit -r backend/app -ll` 由 **exit 1** 变为
  **exit 0（No issues identified）**，远程 CI 的安全步骤不再必然红灯。
  - `app/rag/cache.py:113`、`app/rag/rag_agent.py:1002`：两处 SHA-1 经核实均为**非安全用途**
    （前者是缓存键，后者是跨查询变体去重的定长身份，均在进程内、无持久化契约），
    统一改用 **SHA-256** 并补注释说明用途；同步修正那条断言键长为 40 的用例
    （`tests/test_rag_hybrid_cache.py`，改为 SHA-256 的 64）。
  - `app/main.py`：`uvicorn.run(host="0.0.0.0")` 的 B104 做**精确豁免**（`# nosec B104`），
    豁免理由独立成行写明（容器内需监听所有接口，外部暴露面由 compose 端口映射与网络策略控制）。
    理由行不写在 `# nosec` 同一行 —— 否则 bandit 会把中文说明里的英文词当成测试名解析并告警。

**为什么这么改**：评审给出两条上线阻断项 —— 生产 Worker/Beat 启动路径错误（异步与定时任务
起不来）；CI 的 bandit 步骤必然红灯（2 个 SHA-1 + 1 个 0.0.0.0 绑定）。

**解决了什么问题**：生产异步任务链路可正常启动；CI 安全扫描步骤真正全绿。
剩余 1 项 **B110**（`app/workflows/execution.py:189` 的 `except Exception: pass`）为低危，
低于 CI `-ll` 门槛，不阻断；该处本就是刻意的 best-effort 外部持久化（失败不影响核心调度），
已有代码注释说明，故不做行为改动。

**回归验证**：全量门禁 **27/27 通过（159.3s，绿灯）**；`bandit -r backend/app -ll` exit 0；
受影响的 5 个套件（`test_celery_registration` / `test_rag_hybrid_cache` /
`test_rag_semantic_cache` / `test_rag_session_memory` / `test_rag_enterprise`）单跑全部通过；
全仓库已无 `app.celery_app` 残留引用。

## 2026-09-10 P0 交付阻断项专项：远程 CI / 门禁卡死 / 工程质量 / 生产加固

**提交**：`-`（未提交；生产就绪评审 P0 清单）

**改了什么**：

1. 远程 CI（`.github/workflows/ci.yml`）—— 4 个必过 job：
   - Backend Tests：锁文件安装 + `pip check` + `run_tests.ps1` 全量门禁 + pytest 覆盖率（上传 artifact）
   - Frontend Checks：`tsc --noEmit` + `eslint` + `vitest` + `vite build`
   - Security & Dependency Scan：`pip-audit` + `bandit` + `npm audit` + `gitleaks`
   - Docker Build：后端 / 前端镜像构建

   配合分支保护（Require status checks to pass）即可"PR 未通过禁止合并"；本地 Hook / 脚本不再是唯一防线。

2. 门禁卡死根治（`backend/tests/test_workflow_checkpoint.py` + `run_tests.ps1`）：
   - 根因：该套件模块级 `AsyncEngine`（aiosqlite 内存库）从未 `dispose()`，其连接工作线程为
     **非守护线程（non-daemon）**，解释器 shutdown 会永久等待它 → 套件打印完 `ALL PASSED`
     后进程挂起不退出，整个门禁被卡死。已在 `finally` 中显式 `dispose()`。
   - 门禁每个套件加**独立超时**（默认 300s，`-TimeoutSeconds` 可调）；超时=红灯并
     **终止整个进程树**（Windows `taskkill /T`、Linux `pkill`），stdout/stderr 落盘
     `backend/tests/.gate_logs/` 保留现场，保证门禁能稳定、重复跑完。
   - 门禁执行改用 .NET Process API：PS 5.1 下 `Start-Process -PassThru` 取不到 `ExitCode`
     （得到 `$null`）会误判红灯。
   - Python 解释器探测跨平台（venv → PATH），并把前端 ESLint 纳入本地门禁。

3. 工程质量红灯：
   - ESLint：新增 `frontend/.eslintrc.cjs`，`npm run lint` 由 exit 2 变为通过（0 error）
   - 依赖冲突根治：`packaging` 钉 `<24`（langchain-core 要求）+ `build` 钉 `<1.5`
     （chromadb 依赖 build，而 build>=1.5 又要求 packaging>=24）；三方约束自洽，`pip check` 全绿
   - 锁文件：新增 `backend/requirements.lock`（可复现精确版本），CI 一律以其安装
   - pytest：新增 `backend/pytest.ini` + `tests/pytest_suite/`（应用冒烟 + 生产配置校验），
     CI 产出覆盖率报告
   - 前端测试：新增 vitest + Testing Library，覆盖 API 服务、认证服务、登录页
     （含 antd 会在两个中文字符间插空格的坑）

4. 生产加固：
   - 新增 `compose.prod.yml`：数据服务（Postgres/Redis/Chroma）不暴露端口（internal 网络）、
     后端不挂源码且无 `--reload`、全服务非 root + `no-new-privileges`、CPU/内存/并发限制、
     ChromaDB 固定版本、后端仅绑定回环地址
   - `app/core/config.py`：`DEBUG` 默认 False、新增 `ENVIRONMENT`、默认 `DATABASE_URL`
     去除固定口令、新增 `validate()`——生产环境拒绝默认 JWT 密钥 / 默认库口令 / 开启的 DEBUG
     （启动 fail fast）
   - `app/main.py`：启动调用 `settings.validate()`；生产关闭 `/docs`、`/redoc`、`/openapi.json`
   - Dockerfile：后端以非 root（UID 10001）运行并改用锁文件安装；前端改用非 root nginx（8080）
   - 开发编排改用 `Dockerfile.dev`，与生产镜像彻底分离

**为什么这么改**：P0 阻断项——本地脚本与 Hook 可被绕过、门禁会卡死无法重复跑完、
`npm run lint` 与依赖一致性红灯、生产存在默认密钥/口令、数据端口公网暴露、容器 root 运行等。

**解决了什么问题**：CI 可强制门禁且不可绕过；门禁能稳定重复跑完并保留超时现场；
前端 lint 与 `pip check` 通过、依赖可复现；生产默认安全（拒绝弱密钥、不暴露数据端口、
非 root、限资源），启动即拒绝带病配置。

## 2026-09-04 CI 门禁：统一测试跑批 + 红灯拦截 + 提交前自检（评审阶段一收尾）

**提交**：`-`（未提交；生产就绪评审驱动，对应"无 CI 门禁"问题项）

**改了什么**：
- `run_tests.ps1` 重写为统一门禁：后端套件由"手工维护清单"改为**自动发现**
  `backend/tests/test_*.py`——实际发生过的"新增 `test_workflow_checkpoint.py` 却漏进
  门禁"这类清单漂移不再可能；唯一排除项 `test_workflow_v2.py`（依赖真实 LLM Key 的
  端到端冒烟脚本，仍可 `-Suite` 手动运行）
- 红灯语义：任一套件失败 → 末尾红灯汇总并 `exit 1`；全部通过 → 绿灯 `exit 0`；
  套件级 PASS/FAIL + 耗时明细，失败回显 exit code
- 前端门禁：`frontend/node_modules` 存在时执行 `tsc --noEmit` 类型检查（当前绿）；
  依赖未安装则明确 SKIP，不误伤纯后端环境（注：eslint 缺配置文件是既有基建缺口，
  非本次评审引入，故门禁用 tsc 把关）
- 单套件调试：`run_tests.ps1 -Suite xxx.py`
- 新增 `install_git_hooks.ps1`：安装 `.git/hooks/pre-commit` 钩子，每次 `git commit`
  自动跑全量门禁，红灯中止提交（`CI_SKIP=1` 可临时跳过，例如仅改文档）

**为什么这么改**：评审要求"统一测试跑批 + 失败即红灯 + 提交前自检脚本"，把回归拦在
合入主干之前；原跑批清单靠手工维护，已实际漂移一次（checkpoint 套件从未进门禁）。

**解决了什么问题**：新增/新增断言套件被漏跑、回归漏网；代码不经任何自动检查即可提交。

## 2026-09-04 认证闭环（阻断级安全修复）

**提交**：`-`（未提交；生产就绪评审驱动，对应"认证未闭环"阻断项）

**改了什么**：
- `/auth/me` 修复：`Depends(lambda: None)` 占位 → `get_current_active_user`，真正返回当前用户
- token 分型：access / refresh 携带 `type` + `jti` + `iat`；`deps.get_current_user` 只接受
  access 型 Bearer——refresh token（7 天）不再可冒充 access（此前形同长命后门）
- Refresh Token 落库台账：新增 `auth_sessions` 表（迁移 0007），只存 SHA-256 不落明文，
  带 user_id / family_id / token_hash 索引，支持撤销、轮换与重用审计
- `/auth/refresh`：轮换出新一对 token；旧行标记 `rotated` 并记录 `replaced_by` 去向；
  **重用检测**——已吊销 token 再次出现时整族（family）会话吊销
- `/auth/logout`：吊销当前设备 refresh（幂等；非本人 token 不生效，防止他人误杀会话）
- `/auth/logout-all`：吊销该用户全部会话；access 短窗口（ACCESS_TOKEN_EXPIRE_MINUTES）属预期
- login 顺带清理本人已过期的会话行，台账不无限膨胀
- 测试：新增 `tests/test_auth_closure.py`（23 项 HTTP 级断言，全链路含启动迁移），
  接入 `run_tests.ps1`；`test_migrations.py` 断言更新至 head=0007

**为什么这么改**：评审指出 `/auth/me` 是占位实现，refresh token 签发后无刷新端点、撤销、
轮换与会话管理，认证链路只走通一半。

**解决了什么问题**：登录态自我查询失效；refresh token 可用作任意接口凭证的长命后门；
账号安全事件后无法吊销会话（只能等 7 天自然过期）。

## 2026-09-04 租户隔离 Phase 1：Agent/Task 所有权（阻断级安全修复）

**提交**：`-`（未提交；生产就绪评审驱动，对应"多租户越权"阻断项）

**改了什么**：
- 所有权模型：`Agent` / `Task` 新增 `user_id` 归属列（Uuid、可空 FK `users.id`、带索引）；
  `AgentResponse` / `TaskResponse` 暴露 `user_id`
- 隔离语义：非 admin 用户 list/get/update/delete/cancel/execute 只命中本人资源，
  越权访问一律 404（不泄露资源存在性）；admin 全量可见
- 创建路径：创建 Agent/Task 强制写入 `current_user.id`；创建 Task 引用他人 Agent → 404
- workflow 归档 `_archive_run` 为父/子任务写入 `user_id`，堵住 tasks 表 SQL 层跨用户可见的泄漏口
- 存量数据决策：不 backfill。隔离前的无主记录（user_id IS NULL）普通用户不可见、
  仅 admin 可见，避免历史数据在修复上线瞬间跨用户泄漏
- 迁移 `0006_add_owner_columns_agents_tasks`：SQLite 走 batch（复制建表，规避方言
  ADD COLUMN 不支持内联外键的限制），PG 下等价直接 ALTER；FK 显式命名
- 测试：新增 `tests/test_task_agent_ownership.py`（31 项断言：双用户 + admin + 无主
  存量 + 归档归属），接入 `run_tests.ps1`；`test_migrations.py` 断言更新至 head=0006

**为什么这么改**：生产就绪评审指出 Agent/Task 接口只要求登录、未绑定当前用户，
登录用户可读取/操作他人资源；RAG/记忆模块已做用户隔离，基础资源模块需统一收口。

**解决了什么问题**：Agent/Task 跨用户越权读改删；workflow 归档任务可被任意用户读取。

## 2026-09-03 Workflow 执行引擎（DAG 依赖解析 + 有界并行 + 子任务超时/重试）

**提交**：`-`（未提交；计划见 docs/superpowers/plans/2026-09-03-workflow-execution-engine.md，
配套测试见 tests/test_workflow_execution.py）

**改了什么**：
- 新模块 `workflows/execution.py`：纯异步 DAG 执行引擎（零外部依赖，可离线单测）。
  语义：校验（id 唯一/依赖存在）→ 循环依赖检测（`CyclicDependencyError`）→
  依赖就绪调度 + 有界并发窗口 + priority 降序；每个子任务独立超时
  （`asyncio.wait_for`）与失败重试（attempt 递增，默认额外重试 1 次）；
  下游 context = **仅直接依赖且成功**的任务结果；`skip_on_failure=True` 时
  失败依赖的下游被跳过（默认关闭，保持"尽力继续"语义）
- `workflows/task_planner.py`：LangGraph 图由"顺序自循环"改为阶段状态机
  `analyze_task → run_dag → aggregate_results`；删除 `execute_task` /
  `check_next_task` / `TaskPlanState.current_task_index`；`_execute_by_type`
  重构为 `_run_single_task(task, context_text)`，context 由引擎按依赖注入
- `core/config.py`：新增 `WORKFLOW_MAX_CONCURRENCY`（默认 2）/
  `WORKFLOW_TASK_TIMEOUT_SECONDS`（默认 120）/
  `WORKFLOW_TASK_MAX_RETRIES`（默认 1）
- 测试：`tests/test_workflow_execution.py`（50 项断言，纯离线）覆盖
  校验/环检测/顺序/依赖就绪/context 精确传递/priority/并发窗口与补位/超时/
  重试成功与耗尽/关闭重试/skip 语义/TaskPlanner 集成契约（tasks-results 对齐、
  metadata.recap、无 pending 残留）

**为什么这么改**：
- 编排此前是 LangGraph 顺序 for 循环：`SubTask.dependencies` 被 LLM 产出却从未
  解析使用，互不依赖的子任务无法并行，单任务挂死会拖死整条链，失败只能整轮重跑
- 目标对齐业界先进水平（企业级编排路线图阶段 1）：DAG 化 + 有界并行 +
  子任务级护栏是 checkpoint 持久化 / 断点恢复 / human-in-the-loop 的前置

**解决了什么问题**：
- 独立子任务现在可并行执行（并发上限可配），严格串行的依赖链不再整体阻塞
- 子任务级超时 + 重试：局部故障自动补救，不再需要整轮重跑已完成任务
- 依赖语义真正生效：下游只拿到它声明的直接依赖输出，为真实 DAG 调度打底

---

## 2026-09-03 Workflow 答案语义检索增强（执行档案向量索引）

**提交**：`-`（未提交；配套测试见 tests/test_workflow_answer_search.py，文档见 WORKFLOW_ANSWER_SEARCH_GUIDE.md）

**改了什么**：
- 新模块 `workflows/answer_store.py`：`WorkflowAnswerStore` 把一次 workflow 归档展开为
  「1 条父任务复盘 + N 条子任务结果」文档向量化，落盘独立 Chroma collection
  （`wf_answers`），按 `metadata.user_id` 租户隔离；提供 `index_run` /
  `search`（含 workflow_label/status 过滤）/ `remove_task` / `count`，全部
  失败静默降级不阻断主流程
- `api/workflows.py`：`_archive_run` 新增 `user_id`——归档**落库成功后**自动把
  执行答案语义索引（尽力而为，索引失败仅告警，不影响归档返回）；新增
  `GET /workflows/answers/search`：自然语言检索当前用户的历史执行答案，
  支持 `limit` / `workflow_label` / `status` 过滤；索引不可用返回
  `available=False` 优雅降级
- `core/config.py`：新增 `WORKFLOW_ANSWER_INDEX_ENABLED` /
  `WORKFLOW_ANSWER_PERSIST_DIRECTORY`（默认 `./wf_answer_db`，独立于 RAG
  `chroma_db`，避免同目录多 PersistentClient 锁冲突）/
  `WORKFLOW_ANSWER_COLLECTION`
- Embedding 复用 RAG 配置（`RAG_EMBEDDING_MODEL_TYPE/NAME`），不新增模型
- 测试：`tests/test_workflow_answer_search.py`（40 项断言，纯离线）覆盖文档展开、
  索引/检索/跨用户隔离/过滤/删除/幂等、embedding 失败降级、归档自动索引（含
  user_id 透传）、检索端点鉴权与参数透传、后端不可用降级

**为什么这么改**：
- workflow 归档此前虽已写入 `tasks` 表，但 tasks 无用户字段、只能按标题翻列表，
  长任务执行答案“可查但不好找”；而短/长程记忆只覆盖近窗口会话，无法回答
  “上次代码审查结论是什么”“上个月那次任务规划失败原因”
- 不直接复用 memory/vector_store：排查发现该模块仍按 RAG 多租户重构前的旧签名
  `VectorStoreManager(collection_name=...)` 调用，在本分支已静默失效（异常被吞），
  且 tasks 无 user_id；故建独立、自洽、按用户隔离的答案索引，并在独立目录落盘

**解决了什么问题**：
- 执行答案从“只能翻标题”升级为“可语义追问”：多轮 KB 会话（Phase 5）之外，
  用户/上层可凭自然语言按用户找回历史 workflow 答案，为后续“让 RAG/记忆
  引用历史执行结果”预留干净的入口

---

## 2026-09-03 RAG 会话版问答（Phase 5：多轮 KB 问答）

**提交**：`b5b2f2a`（配套测试 `6cb9ed0`；配套文档见 RAG_USAGE_GUIDE v1.2）

**改了什么**：
- `rag_agent.py`：`execute` 新增可选 `session_id` / `db_session` 参数——
  传入即开启会话版问答：按 用户+会话 构造 MemoryManager，取会话上下文注入
  答案生成（单独的“聊天上下文”块，仅辅助消解指代、不作为事实依据），并自动
  记录 user/assistant 消息（命中 per-user 缓存的首轮也会补记 assistant 消息，
  保持消息成对连续）；结果透出 `session` 元数据
  （enabled / context_active / cache_bypassed / 失败原因）
- **缓存安全约定**：会话上下文非空（含跨会话记忆检索命中）时自动旁路 per-user
  语义缓存（`cache.reason="session_context_active"`），避免跨上下文返回陈旧答案；
  会话首轮（上下文为空）与不传 session_id 的无状态查询照常参与缓存
- **容错降级**：记忆层任何失败（DB/Redis 不可用）都降级为无状态 RAG，
  绝不让记忆问题阻断核心问答；`set_memory_factory` 支持测试注入内存替身
- `/rag/query`：`QueryRequest` 新增 `session_id`，路由注入请求级 DB 会话
  （传 session_id 时才使用）；`/rag/info` 版本升至 1.2 并展示 session_memory 能力
- 测试：`tests/test_rag_session_memory.py`（22 项断言，纯离线替身）覆盖首轮缓存
  照常参与/后续轮旁路缓存并重新生成/消息成对记录/跨用户跨会话隔离/记忆层故障降级

**为什么这么改**：
- 此前 `/rag/query` 是无状态单轮：同样的追问（“那它有哪些限制？”）每次都丢失
  前文，用户需要反复把背景塞进问题；而记忆系统（短/长程 + 持久化）与 RAG 彼此
  独立、没有打通
- 上一轮“Agent 步骤级语义缓存”经设计推演确认收益极低（每轮执行都会推进会话
  上下文，相同快照几乎不出现，且有过时答案风险），故转向真正缺口的“会话×RAG”

**解决了什么问题**：
- 单轮 KB 问答升级为多轮会话问答：同一 `session_id` 下追问自动携带前文，
  user/assistant 消息入库可被记忆检索复用
- 多轮正确性与缓存成本不冲突：上下文激活即旁路、上下文为空即照常命中，
  行为可观测（`session` 元数据 + cache reason）

---

## 2026-09-03 Agents/Workflows 挂载会话记忆

**提交**：`8c84837`（配套测试 `0027016`）

**改了什么**：
- `BaseAgent` 记忆装载重写（此前 `set_memory/execute_with_memory` 存在但零调用、且输入输出键不匹配任何具体 Agent）：
  - 新增 `attach_memory(memory_manager)`：挂载**已构造**的 manager（多 Agent 共享同一会话，不重复初始化）
  - `set_memory` 保留签名，返回 manager 便于复用
  - `execute_with_memory`：**深拷贝防污染**（不再向调用方 dict 塞 `memory_context`）；`get_context()` 结果截断至 4000 字符注入 `memory_context` 键；用户消息按 `user_input/requirement/question/query` 提取（仅有 code 时加"请审查以下代码"前缀）；助手消息按 `explanation/review/summary/output/answer` 提取、code 截断兜底 —— Coder/Reviewer 的键适配补齐
- CoderAgent / ReviewerAgent 组装 prompt 时消费 `memory_context`，生成与审查轮次可参考历史会话（无记忆时行为不变）
- `BaseWorkflow` 新增 `memory_manager` 构造注入 + `ensure_memory(initial_state)`（支持 `initial_state["memory"]` = session_id/user_id/db_session 配置自动建 manager）+ `memory_info()`（结果回传会话元信息）
- CodeReviewWorkflow / TaskPlannerWorkflow：开始执行时装载记忆并 attach 到**所有**参与 Agent（TaskPlanner 对动态创建的子 Coder/Reviewer 同样挂载），节点调用改为 `execute_with_memory`；结果 `metadata.memory` 回传 `{enabled, session_id}` —— 一次工作流内多 Agent 轮次汇聚到同一会话，跨请求带上 session_id 即可延续
- HTTP：`POST /api/v1/workflows/code-review`、`/task-planner` 支持 `enable_memory` + 可选 `session_id`（不传自动生成并随 metadata 返回），复用请求 DB 会话持久化
- 文档：`MEMORY_USAGE_GUIDE.md` 新增「Workflow 会话记忆」小节并修正 execute_with_memory 键适配说明
- `backend/tests/test_workflow_memory.py`（40 项断言）：FakeMemoryManager + mock Agent **纯离线**覆盖消息键适配、上下文注入/防污染、Coder/Reviewer 实际适配、两工作流共享记忆与 metadata 回传、未启用记忆零回归

**为什么这么改**：
- 记忆模块早已具备（短/长程 + 持久化），但"接入 Agent/Workflow"只是纸面接口：Coder 产出 `code` 而 execute_with_memory 只认 `output`，workflow 从不挂载 —— 记忆实际上不可用
- Workflow 是记忆最自然的载体：多 Agent 多轮次共享会话，让"上下文连续"不再依赖每次请求把全量历史塞进 input

**解决了什么问题**：
- 记忆从"可调用的库"变成"开箱即用的会话能力"：一行 `enable_memory` + 同一 `session_id`，跨请求/跨 Agent 上下文自动衔接
- 修复输入污染与键不匹配两个潜在缺陷；未启用记忆路径与旧行为严格一致（全量回归通过）

---

## 2026-09-03 RAG 语义缓存升级（真·语义命中层）

**提交**：`8a5b9f6`（配套测试 `2af31b4`）

**改了什么**：
- `rag/cache.py`：原 `SemanticCache` 实为**逐字精确哈希**缓存，升级为「精确 + 语义」
  两级——条目除 key 外记录 query 文本 / pipeline profile / query 嵌入向量；
  新增 `lookup()`（先精确、后对**同用户同 profile** 条目做嵌入余弦比较，
  相似度 ≥ 阈值即复用）、`store()`（带元数据回填，兼容原 `put/get` 契约）
- 嵌入**懒调用**：lookup 仅精确未命中才调用嵌入器（async embedder 注入），
  精确命中零嵌入开销；miss 时扫描向量随 match 回传，回填复用避免二次嵌入
- 隔离与安全：profile（search_type|k|管道标签）与租户双重隔离、余弦阈值保护
  （低于阈值宁可重新生成，杜绝无关问法串答案）、维度不一致/嵌入失败/TTL 清扫
  静默退化，不影响主流程
- 统计扩展：`exact_hits / semantic_hits / semantic_attempts / near_misses`
  （near_misses 为阈值调优信号），随 `get_knowledge_base_stats` 透出到 `/rag/stats`
- `rag_agent.py`：缓存块改用 `lookup/store`，语义命中回写当前问法并标注
  `cache.kind="semantic"` + `matched_query` + `score`；config/环境变量新增
  `RAG_CACHE_SEMANTIC_ENABLED / _THRESHOLD / _MIN_QUERY_LEN`
- 测试：`tests/test_rag_semantic_cache.py`（36 项断言，纯离线确定性嵌入）
  覆盖语义命中/精确零嵌入/隔离/阈值/维度/TTL/LRU/disabled 与 agent 端到端
  （改述命中跳过检索复用答案、原句精确命中、无关问法 below_threshold）
- 文档：`RAG_USAGE_GUIDE.md` 新增语义缓存配置示例、FAQ Q6 与 v1.1 更新日志

**为什么这么改**：
- 名为「语义缓存」实为 exact-hash：改述/近似问法全部 miss，重复检索 + LLM 生成
- 把"省一次 LLM"的收益从「完全同文重复」扩展到「语义等价问法」，命中率大幅上探

**解决了什么问题**：
- 近似问法不再重复消耗检索与生成成本；跨用户 / 跨管道答案互不串用
- 命中来源可观测（exact vs semantic + 相似度），阈值有 `near_misses` 可调信号

---

## 2026-09-03 Workflow 长任务复盘（Task Recap）与归档

**提交**：`e7bbd85` + `c41ccf8`（配套测试 `7d5dcaa`）

**改了什么**：
- 新增 `workflows/recap.py`：`build_recap()` 结构化复盘生成器（workflow 标签、目标、
  成功与否、尝试/迭代次数、任务汇总、子任务明细、备注；纯函数、确定性、零外部依赖）
  与 `format_recap()` 可读文本渲染（Markdown 风格，用于写入记忆与展示）
- `BaseWorkflow.record_recap(text)`：仅在启用会话记忆时将复盘以
  `kind=task_recap` 的 assistant 消息写入共享会话；写入失败静默降级不阻断主流程
- CodeReview / TaskPlanner：**执行结束自动生成复盘**并随 `metadata.recap` 回传
  （成功与重试耗尽两种返回路径均覆盖）；复盘随后续 `get_context()` 自然携带，
  同会话"上次任务做到哪、结果如何"无需再拼进请求
- HTTP（`POST /api/v1/workflows/code-review`、`/task-planner`）新增 `archive` 选项
  （默认 true）：执行结果自动归档进 `tasks` 表——父记录为一次执行复盘
  （title 形如 `[任务规划] 需求`），TaskPlanner 各子任务作为子记录落库
  （`GET /api/v1/tasks` 即可查询历史执行档案）；归档尽力而为，DB 异常不影响主流程
- 修复既有缺陷：`CodeReviewState` 未声明 `structured_review` 通道，langgraph 按
  TypedDict 模式丢弃该键，导致 `metadata.has_structured_review` 恒为 False、
  API 返回的 `structured_review` 永远为 None——已补声明并回归验证
- 测试：`tests/test_workflow_recap.py`（41 项断言，纯离线）覆盖复盘结构/文本渲染、
  record_recap 记忆写入与静默降级、两工作流自动复盘与记忆写入、无记忆时仍回传复盘
- 文档：`MEMORY_USAGE_GUIDE.md` 新增「任务复盘（Task Recap）」小节

**为什么这么改**：
- 第 4 轮打通了"消息级会话记忆"，但长任务跑完没有结构化的**执行留痕**：
  用户无法从记忆/档案里知道上一次任务规划到底做了哪几个子任务、哪些失败
- `tasks` 表与 tasks CRUD 早已存在却没有任何 Workflow 写入——自动归档让其成为
  真正的"长任务执行档案"

**解决了什么问题**：
- 长任务从"执行完即忘"变成"可复盘、可延续、可查询"：同一会话再次启动时上下文
  自动带上上次复盘；跨会话可在任务列表中翻查历史执行档案
- 顺带修复结构化审查结果一直无法透出 API 的既有缺陷

---

## 2026-09-03 RAG RAGAS 离线评估框架

**提交**：`09526e4`（配套测试 `71990d3`）

**改了什么**：
- 新增 `backend/app/rag/evaluator.py`：RAGAS 指标评估器（可选依赖、顶部零导入）
  - 指标：`faithfulness / answer_relevancy / context_precision / context_recall`（含中文说明、默认全开、未知指标校验）
  - 样本归一化：`question/answer/contexts/ground_truth`，容错 `ground_truths` 别名与字符串→列表
  - **自动适配 ragas 两代 API**：0.1.x（HF Dataset + `metrics.base.set_llm/set_embeddings`）与 0.2.x（`EvaluationDataset/SingleTurnSample`，llm/embeddings 走 evaluate 参数）；未安装/版本不支持 → 带安装指引的 `RAGEvaluationError`，绝不拖垮主链路
  - 结构化报告：指标均值 + 逐问题明细（NaN/缺失归一为 None，均值只统计有效值）
- `RAGAgent.execute` 增可选 `include_full_documents`：携带模型实际看到的**全文片段**（结果中的 `retrieved_documents` 为 200 字符预览，供评估不可用）；不进语义缓存快照，默认关闭零影响
- 端到端 runner `backend/examples/rag_eval_runner.py`：自包含数据集（corpus+questions）导入隔离租户 → 逐问跑完整实时链路（自动关闭语义缓存）→ RAGAS 打分 → 写 JSON 报告；支持 `--no-rerank/--no-transform` 做 A/B 对照
- 示例数据集 `backend/examples/rag_eval_dataset.example.json`（3 篇语料 + 5 问）；可选依赖清单 `backend/requirements-eval.txt`（`ragas>=0.1.10`）
- 文档：新增 📖 `docs/RAG_EVALUATION_GUIDE.md`；FAQ Q4 更新为具体指标与开箱步骤；架构文档 v2.0 路线进度同步
- `backend/tests/test_rag_eval.py`（38 项断言）：假 ragas/datasets 注入，**离线**覆盖归一化、指标校验、报告聚合、legacy/v2 双代适配、LLM/Embedding 装配时机、未安装降级

**为什么这么改**：
- 「查询转换/重排」这类改造必须能量化收益，否则无从证明价值；RAGAS 是业界通用离线评估标准
- 平台坚持零重依赖与可离线测试：ragas 仅在评估命令真正运行时惰性加载，编排逻辑全部用假模块回归

**解决了什么问题**：
- 建立「检索质量 → 生成质量」的量化基线：context_recall 低提示调大 k/开启查询转换，context_precision 低提示开启重排
- 评估公平性：contexts 用模型真实看到的全文（非截断预览），缓存关闭保证测的是实时链路
- 开发者无需懂 ragas 内部：一份自包含数据集 + 一条命令即可出报告

---

## 2026-09-03 RAG 查询转换（LLM 多查询扩展）

**提交**：`d1bcdd0`（配套测试 `fc0d497`）

**改了什么**：
- 新增 `backend/app/rag/query_transformer.py`：检索前用 LLM 一次调用把用户问题改写成多个检索变体（**多查询扩展**）；输出恒以原文开头，保证基线召回不劣化
- 变体解析：逐行清洗（编号/项目符号/包裹引号）+ 规范化去重 + 数量封顶；与原文重复的变体丢弃；LLM 前言废话被容忍为无害变体（检索无果即无贡献）
- `RAGAgent.execute` 多段式升级：**查询转换 → 多变体逐路召回 → RRF 融合去重 → 两阶段重排 → 上下文/答案**；单变体（未启用/短查询/无 LLM）时与旧路径完全一致
- 多变体融合复用既有 RRF 常数（`RAG_HYBRID_RRF_K`），片段去重身份优先 `chunk_id`，缺失时内容摘要
- **缓存键再区分管道**：`extra` 因子叠加 `rerank|transform`，转换开/关的结果互不复用；缓存命中不触发任何 LLM 阶段；结果带 `transformation.{enabled,variants,variant_count}` 元信息
- 配置：`RAG_TRANSFORM_ENABLED(True) / NUM_VARIANTS(3) / MIN_QUERY_LEN(8)`；`capabilities` 增 `query_transformation`，`/info` 增 `query_transformation` 块与组件说明
- `backend/tests/test_rag_query_transform.py`（40 项断言）：变体解析、转换器降级矩阵、Agent 端到端（变体补漏召回被单查询漏检的文档）、缓存命中不调 LLM、管道键隔离、无 LLM 保持旧行为

**为什么这么改**：
- 单条查询只能表达一个角度，容易漏掉同一问题的其它侧面；改写后的变体各自召回可显著提升召回率
- 在已有「召回放大 → RRF → LLM 重排」链路上，查询转换是对"漏检"最直接的补强手段

**解决了什么问题**：
- 覆盖单查询漏检：原问题召不到的片段，通过侧面变体召回并 RRF 融合进候选
- 成本/风险可控：一次 LLM 调用生成全部变体；每变体召回预算按 `stage1_k/变体数` 收缩，总候选量不变，仍由重排精排把关
- 与旧版零行为差异：未启用/短查询/无 LLM 一律走单查询原路径，既有 14 套件全部保持通过

---

## 2026-09-03 RAG 两阶段重排（LLM 点级打分）

**提交**：`e0c5a8a`（配套测试 `d6ba17d`）

**改了什么**：
- 新增 `backend/app/rag/reranker.py`：两阶段检索的第二段 —— 第一阶段按 `min(k×系数, 上限)` 放大召回，再用 LLM 对候选**批量点级打分**（单次调用，0.0~1.0）排序并截断回 top-k
- 分数解析采用**严格逐行规则**：去掉空行后必须恰好 N 行纯数字，否则降级原序 —— 候选正文里的数字不会被误抓成错位分数
- 健壮性：未配置 LLM / LLM 调用异常 / 输出不可解析 → 一律降级原序返回，重排绝不中断检索链路；同分稳定保持召回顺序
- `RAGAgent` 装配重排：`execute` 变为 召回放大 → 重排截断 → 构建上下文 三段式；未启用重排时 stage1_k=k，**与旧行为完全一致**
- **缓存键区分管道**：`SemanticCache.make_key` 增加 `extra` 因子，是否重排的答案互不复用；结果带 `rerank.{enabled,candidates,final,scores}` 元信息
- 配置：`RAG_RERANK_ENABLED / CANDIDATE_MULTIPLIER(3) / MAX_CANDIDATES(30) / MAX_DOC_CHARS(600)`；`capabilities` 增 `reranking`，`/info` 暴露重排配置
- `backend/tests/test_rag_reranker.py`（35 项断言）：分数解析、单元重排/降级/稳定排序、Agent 端到端（放大召回→重排截断→缓存区分→ingest 失效）

**为什么这么改**：
- 混合检索的 RRF 只是"排序信号融合"，对 query 语义的细粒度相关性判断仍是弱信号（向量/词法都基于表层匹配）
- 业界标准做法是召回后用更强的重排器精排 top-k 进入 LLM 上下文 —— 直接减少噪音进上下文、提升答案质量、并省 token

**解决了什么问题**：
- 上下文噪音：进 LLM 的片段由相关性打分重排把关，而不是只信召回阶段顺序
- 与既有体系一致：零新依赖、事件失效缓存继续生效、开关默认开但无 LLM 自动旁路、不改变未启用时的任何行为

---

## 2026-09-03 RAG 混合检索（BM25+向量+RRF）+ 语义缓存

**提交**：`82972b9`（配套测试 `3cad018`）

**改了什么**：
- 默认检索策略升级为 **hybrid**：BM25 词法路 + 向量语义路 双路召回 + RRF 融合（`search_type=hybrid`，API 校验同步放开）
- 新增 `backend/app/rag/lexical.py`：自实现 Okapi BM25（零新依赖），分词同时支持中英文（英文按词、中文按单字）；按用户分区的倒排统计懒构建 + 脏标记增量重建
- 新增 `backend/app/rag/fusion.py`：`reciprocal_rank_fusion` 纯函数融合
- `backend/app/rag/vector_store.py`：切块写入时注入 `chunk_id` 作为融合对齐锚点；`add_chunks/delete_document_chunks/delete_user_collection` 同步维护词法索引；新增 `hybrid_search`；**进程重启后首次 hybrid 查询自动从 Chroma 懒重建词法索引**（避免"词法只活在重启前"）
- 新增 `backend/app/rag/cache.py`：每用户作用域语义缓存（查询→答案），LRU 容量裁剪 + TTL 兜底 + **知识库变更事件失效**（ingest 新增 / 删除文档 / 清空自动清缓存），可关闭旁路
- `backend/app/core/config.py`：新增 `RAG_LEXICAL_ENABLED / RAG_HYBRID_FETCH_MULTIPLIER / RAG_HYBRID_RRF_K` 与 `RAG_CACHE_ENABLED / RAG_CACHE_TTL_SECONDS / RAG_CACHE_MAX_ENTRIES_PER_USER`
- 能力面如实暴露：`capabilities` 含 `hybrid_search`、`semantic_cache`，`search_strategies` 首项为 `hybrid`；`execute` 返回带 `cache.hit/key` 状态
- `backend/tests/test_rag_hybrid_cache.py`（43 项断言）：RRF 融合、中英文分词、BM25 相关性/生命周期/幂等/重建、缓存命中/TTL/LRU/每用户隔离/事件失效、Agent 端到端 hybrid+缓存

**为什么这么改**：
- 纯向量相似度对"关键词精准命中、专有名词、缩写"场景召回弱（语义向量不擅词级匹配）；混合检索是当前企业级 RAG 的召回标准做法
- 上一批已引入向量库持久化，但"每次查询都重算 LLM 答案"成本高、响应慢；同查询短时重复在企业对话中很常见

**解决了什么问题**：
- 召回质量：词法精确命中 + 语义泛化互补，RRF 融合无需调权
- 成本与延迟：重复查询直接命中缓存（省去向量检索 + LLM 生成）；知识库变更即时失效，答案不陈旧
- 词法索引生命周期闭环：与向量层同步维护 + 重启后懒重建，长期可依赖

---

## 2026-09-03 RAG 文档级持久化 + 多租户文档管理 API

**提交**：`a482e58`

**改了什么**：
- 新增模型 `RAGDocument`（表 `rag_documents`）与迁移 `0004`：文档级元数据持久化（user_id / filename / file_type / sha256 checksum / chunk_count / collection_name / status / error_message），`(user_id, checksum)` 唯一约束支撑幂等导入
- 新增 `backend/app/rag/repository.py`：文档记录仓储层（幂等查找 / 创建 / 列表 / 按用户删除），数据访问集中化，便于测试注入
- `backend/app/core/config.py`：新增 `RAG_*` 配置段（持久化目录、collection 前缀、切块/检索参数、Embedding 后端、上传大小与扩展名白名单、分页大小），消除硬编码
- `backend/app/api/rag.py` 重写：新增 `GET /documents`（分页列表）与 `DELETE /documents/{id}`（删除单文档：向量切块 + 元数据记录），`DELETE /clear` 与 `GET /stats` 改为按用户作用域；上传安全落盘（uuid 文件名防路径穿越）、扩展名白名单（415）、大小上限（413）、领域异常统一映射明确状态码
- `backend/tests/test_rag_enterprise.py`（27 项断言）：多租户隔离 / 幂等导入 / 文档管理作用域 / 强制 user_id / 跨租户检索内容级验证
- `backend/tests/test_migrations.py`：head 推进 `0004`，新增 `rag_documents` 表/索引/唯一约束断言
- `backend/examples/rag_demo.py`：适配多租户接口
- `run_tests.ps1`：纳入 `test_rag_enterprise.py`

**为什么这么改**：
- 旧实现中 `ingest` 结果只活在 Chroma 本地文件，无任何业务持久化——没有文档清单、无法做文档级删除/权限/审计，也无法幂等（同文档重复导入产生重复向量）
- 文档级管理是"能不能上生产"的红线之一：没有它，多租户隔离只解决了读取边界，管理面（删除/清空/审计）无从谈起

**解决了什么问题**：
- 文档级生命周期闭环：上传 → 列表 → 删除单文档 → 清空，全部按当前登录用户作用域生效
- 幂等导入：重复上传相同内容自动跳过（`skipped_duplicate`），杜绝知识库膨胀
- 错误语义透明：文件类型/大小/越权删除等场景返回明确 HTTP 状态码，不再"吞错返回 200"

---

## 2026-09-03 RAG 多租户隔离 + 异步安全向量库

**提交**：`3c3ac82`

**改了什么**：
- `backend/app/rag/vector_store.py` 重构：从"全局单 collection"改为**每用户独立 Chroma collection**（`rag_{user_id_hex}`，前缀见 `RAG_COLLECTION_PREFIX`）；全部同步 Chroma 调用经 `asyncio.to_thread` + 内部锁串行化；切块元数据携带 `user_id / doc_id / collection` 支持按文档整删；后端不可用统一抛 `RAGBackendError`
- `backend/app/rag/retriever.py`：所有检索方法强制携带 `user_id`，只查该用户自己的 collection（物理隔离）
- `backend/app/rag/rag_agent.py` 重构：`execute/ingest_documents/list_documents/delete_document/delete_all_documents` 全部以 `user_id` 为租户边界（缺失即拒绝）；新增 `configure_components` 支持测试注入；文档导入按 sha256 幂等并持久化元数据
- 新增 `backend/app/rag/exceptions.py`：领域异常族（UnsupportedFileTypeError / FileTooLargeError / EmptyDocumentError / DocumentNotFoundError / RAGBackendError）
- `backend/tests/test_rag_enterprise.py`（27 项断言）与本记录配套（多租户隔离与幂等部分）

**为什么这么改**：
- 旧实现所有用户共享同一个 `rag_default` collection——**任何用户都能检索到任何用户上传的文档**，检索层完全没有租户边界（安全事件级缺陷）
- 旧 `VectorStoreManager` 绑定单一 collection，同步 Chroma 调用直接阻塞 async 事件循环
- 旧 `RAGAgent.execute` 无 `user_id` 约束，"无租户上下文也可检索"是数据泄漏的根源

**解决了什么问题**：
- 多租户数据隔离从"检索时过滤"升级为"collection 物理隔离"，缺 filter 也不会串库
- 检索/写入全程不阻塞事件循环；并发操作被内部锁串行化，避免 Chroma 客户端竞态
- 未携带 `user_id` 的任何 RAG 操作被硬性拒绝，杜绝"无主检索"路径

---

## 2026-09-02 Celery worker 启动注册 memory 任务（接线修复）

**提交**：`791de3c`

**改了什么**：
- `backend/app/core/celery_app.py`：`Celery(...)` 构造函数新增 `include=["app.tasks.memory_tasks"]`，worker/beat 进程启动即导入注册 `memory.consolidate` 与 `memory.decay_memories`
- 新增 `backend/tests/test_celery_registration.py`（9 项断言）：模拟 worker 启动路径（`loader.import_default_modules()`）后任务已注册、beat_schedule 指向已注册任务且周期与 `MEMORY_DECAY_INTERVAL_SECONDS` 一致、decay 任务可靠性属性（`acks_late` / retry）不回退
- `run_tests.ps1`：纳入新套件

**为什么这么改**：
- `autodiscover_tasks(["app.tasks"])` 实际查找的是 `app.tasks.tasks` 模块（不存在）；任务真正定义在 `app/tasks/memory_tasks.py`
- 此前该模块仅被 API 进程内的 `manager.py` 延迟 import（运行期触发），**worker 进程冷启动时从未 import 过它**——`memory.*` 任务在 worker 端未注册，beat 每 6 小时投递的 `memory.decay_memories` 会因 "Received unregistered task" 永远无法执行

**解决了什么问题**：
- 定时衰减/过期归档从"代码正确但无人调度执行"变为真正可运行：beat 投递 → worker 已注册 → 执行
- 冷启动场景下 consolidation 任务同样受惠（不再依赖 API 进程恰好先触发过 import）
- 回归测试用 worker 视角固化接线，防止未来 autodiscover/include 配置回退

---

## 2026-09-02 后台批量扫描索引（迁移 0003）

**提交**：`35a5c5b`

**改了什么**：
- `backend/app/models/memory_entry.py`：`__table_args__` 新增复合索引 `ix_memory_archived_strength_updated (archived_at, strength, updated_at)`
- 新增迁移 `backend/alembic/versions/0003_add_memory_decay_scan_index.py`
- `backend/tests/test_migrations.py`：head 断言推进到 `0003`，新增后台批量扫描索引存在性检查

**为什么这么改**：
- 后台批量任务（定时衰减 + 合规过期归档）的扫描查询是 `WHERE archived_at IS NULL AND strength IS NOT NULL`，**不带 user_id 前缀**
- 现有索引全部以 `user_id` 开头（检索路径专用），批处理无法命中，只能全表扫描；归档条目随业务增长后成本线性上升

**解决了什么问题**：
- 批处理只需扫描活跃子集（`archived_at IS NULL` 前缀定位），归档数据越多收益越明显
- 与方向"批量衰减任务归档合规过期记忆"（`5d7fe2c`）配套：过期清理真正落库后，索引保证清扫本身高效

---

## 2026-09-02 批量衰减任务归档合规过期记忆

**提交**：`5d7fe2c`

**改了什么**：
- `backend/app/memory/decay.py`：`decay_memories` 在应用时间衰减前先判断 `expires_at <= now`——到期的合规记忆**直接软归档**（不衰减、不等待强度降阈值）
- 返回值新增 `expired` 字段（因过期而归档的条目数），`decayed` 改为只统计真正做了时间衰减的条目，`archived` 语义不变（低强度 + 过期归档总数）
- 归档条目统一进入向量索引清理（复用既有 `archived_ids` 路径）
- 新增 `backend/tests/test_memory_decay_expiry.py`（5 项断言：到期归档 / 未到期保留 / 无约束保留 / `expired` 计数 / 过期条目跳过衰减）
- `run_tests.ps1`：纳入新套件

**为什么这么改**：
- `expires_at`（合规保留策略）此前只在检索层被过滤——到期条目只是"不可见"，**从未有任何任务真正落库归档**，过期数据在表中永久留存，随会话与事件累积只增不减
- 原衰减只归档"强度低于阈值"的条目；一条 `strength=0.9` 的过期记忆永远不会因衰减被归档，合规到期形同虚设

**解决了什么问题**：
- 合规保留策略闭环：到期（`expires_at`）→ 批量衰减任务（Celery beat 周期路径）→ 软归档（`archived_at`，保留审计轨迹）→ 检索/向量索引全部排除
- 过期条目不再做无意义的时间衰减与 `updated_at` 扰动
- 回归测试固化该行为（含 `expired` 计数与既有多 key 返回结构向后兼容）

---

## 2026-09-02 记忆检索归档过滤复合索引（迁移 0002）

**提交**：`08e89bb`

**改了什么**：
- `backend/alembic/versions/0002_add_memory_archive_index.py`：新增迁移——在 `memory_entries` 建复合索引 `(user_id, archived_at, strength, updated_at)`
- `backend/app/models/memory_entry.py`：模型同步增加该索引（create_all 兜底路径一致）
- `backend/tests/test_migrations.py`：断言迁移版本到 `0002` 且新索引存在

**为什么这么改**：
- 检索主查询是 `WHERE user_id = ? AND archived_at IS NULL ORDER BY strength DESC, updated_at DESC LIMIT n`；旧复合索引 `(user_id, strength, updated_at)` **无法过滤归档行**——用户条目增长（event 累积、历史归档）后每次检索都要扫描含归档行的全量集合，随归档量增长性能劣化

**解决了什么问题**：
- 活跃条目检索路径（用户 + 未归档 + 强度/时间排序）被索引完整覆盖，检索成本只与活跃条目数相关
- 已有库通过 `alembic upgrade head` 自动应用；全新库 0001→0002 顺序建表
- 迁移回归测试固化（版本号 + 索引存在性）

---

## 2026-09-02 删除会话时归档其记忆条目（孤儿数据防护）

**提交**：`6ab3f11`

**改了什么**：
- `backend/app/memory/persistence.py`：`delete_conversation` 在删除会话及其消息的同时，**软归档该会话产生的全部记忆条目**（`archived_at` 标记，保留审计轨迹）
- `backend/tests/test_e2e_memory.py`：新增 2 项检查（删除会话接口 + 删除后该会话记忆不再被检索）

**为什么这么改**：
- `GET /entries` 与语义检索均按 `user_id` 过滤，不区分会话；旧实现删除会话只删 `conversations` 与 `messages`，**该会话沉淀的 MemoryEntry 全部残留**
- 用户删除会话后，其记忆（如咖啡偏好、个人信息）依然被跨会话检索召回 → 隐私与一致性问题

**解决了什么问题**：
- 删除会话后该会话的记忆条目不再参与任何检索（已归档，检索过滤 `archived_at IS NULL`）
- 保留审计轨迹（软删除，与 `DELETE /entries` 行为一致）
- `manager.clear()` 清空记忆的语义随之完整（会话级清理包含其记忆条目）

---

## 2026-09-02 e2e 补充：长期摘要跨请求可见性验证

**提交**：`b96dcce`

**改了什么**：
- `backend/tests/test_e2e_memory.py`：新增 2 项检查
  - `GET /api/v1/memory/{sid}/context` 端点可用性
  - `long_term_summary` 跨请求可见且含会话消息内容（咖啡/吉他）

**为什么这么改**：
- 审计摘要链路时确认 `initialize → load_summary(metadata_["summary"]) → long_term.set_summary` 与 `consolidate → metadata_["summary"]` 是同一条路径，闭环完整
- 但该闭环此前**无任何测试固化**：若未来 `load_summary` 读错字段或 consolidate 不再写回，摘要跨请求丢失将静默发生

**解决了什么问题**：
- 用真实 HTTP 链路固化「consolidation 摘要落库 → 跨请求加载可见」的契约，防止回归
- 补上 `GET /context` 端点本身的 e2e 覆盖

---

## 2026-09-02 测试基建：强制退出码与一键回归

**提交**：`a8763ee`

**改了什么**：
- `backend/tests/test_memory_improvement.py`：
  - `main()` 增加**强制退出码**：任何一项失败（`test_no_semantic_loss` 返回 False / 窗口测试异常）时 `sys.exit(1)`
  - `test_window_overflow_detection` 返回 `bool`，纳入结果
- 新增 `run_tests.ps1`：一键运行全部 9 个记忆测试套件，任一失败整体退出码非 0（可用于 CI）

**为什么这么改**：
- `test_memory_improvement` 是打印式测试：语义检查失败时只打印提示，**进程仍以 0 退出**——本次 mock 摘要 bug（关键信息全部丢失）就是在这种静默下长期未被发现的
- 回归验证此前依赖手工逐条命令，无法保证全量覆盖与失败可见性

**解决了什么问题**：
- 任何测试失败都会产生非 0 退出码，CI/回归脚本可可靠捕获
- `run_tests.ps1` 一键全量回归，缺 venv 时给出明确提示

---

## 2026-09-02 启发式记忆提取：过滤一次性指令与提问

**提交**：`d5a6bf8`

**改了什么**：
- `backend/app/memory/extractor.py`：
  - 新增 `_HEURISTIC_COMMAND_MARKERS`（帮我/请你/能否/请解释/帮我写…）与 `_HEURISTIC_QUESTION_PREFIX`（为什么/怎么/如何/请/介绍/解释…）两类信号
  - 启发式提取前先判定 `_is_command_or_question`，命中则跳过该消息
- `backend/tests/test_memory_extractor_heuristic.py`：新增 9 项提取质量回归测试

**为什么这么改**：
- 无 LLM 时启发式提取只按关键词匹配，**指令与问题也会命中偏好/事实关键词**：
  - 「请使用 Redis 做缓存」→ 命中 fact（使用/redis）→ 被当长期事实
  - 「为什么 Python 比 Java 快」→ 命中 fact（python）→ 被当事实
  - 「我希望你能帮我部署项目」→ 命中 preference（希望）→ 被当用户偏好
- 这些一次性指令/提问沉淀后长期污染记忆库，检索时反复召回无关内容

**解决了什么问题**：
- 指令与提问不再误提取为长期记忆，记忆库只沉淀真实偏好/事实
- 真实偏好（「我喜欢用空格缩进」）与事实（「项目采用 FastAPI」）提取不受影响
- LLM 调用失败降级到启发式路径时同样受益

---

## 2026-09-02 mock 模式长期记忆摘要质量（无 LLM 降级路径）

**提交**：`9ee9947`

**改了什么**：
- `backend/app/memory/long_term.py`：无 LLM 的 mock 模式摘要从「占位符」改为**增量拼接真实消息内容**（`role: content` 换行累积），受 `max_summary_length` 限制截断
- `backend/tests/test_memory_summary_mock.py`：新增 8 项摘要质量回归测试
- 修复后 `test_memory_improvement.py::test_no_semantic_loss` 的 4 项语义断言（学习Python / 数据类型 / 列表元组 / 学生管理）全部由「丢失」变为「已保留」

**为什么这么改**：
- 旧实现每次 `add_message` 把摘要覆盖为 `"[Mock Summary] Recent N messages"`：
  - 摘要**不含任何消息内容**，长期记忆对检索/展示零信息量
  - `set_summary(旧摘要)` 后调用 `add_message` 会把旧摘要**覆盖**，增量累积失效——每次整合只反映最近一个批次，历史脉络全部丢失
- `test_no_semantic_loss`（既有测试）的语义完整性断言此前实际全部失败，但因打印式测试无 exit code 校验而未被发现

**解决了什么问题**：
- 无 LLM 环境（本地/生产降级）下长期记忆摘要保留真实消息内容
- 增量整合（旧摘要 + 新批次）正确累积，不丢历史
- 摘要长度有界（`max_summary_length` 截断）

---

## 2026-09-02 短期记忆窗口恢复幂等（跨请求/跨 worker）

**提交**：`a694c4c`

**改了什么**：
- `backend/app/memory/manager.py`：`initialize` 恢复短期记忆窗口前先检查 store 是否已有数据；**仅当 store 为空时**才从 DB 重建窗口
- `backend/tests/test_memory_short_term_restore.py`：新增 3 项恢复幂等回归测试

**为什么这么改**：
- `initialize` 每请求执行，旧实现无条件把 DB 最近窗口**追加**进 store
- 进程内 store 每次请求新建（空），追加合理；但 **Redis store 跨请求/跨 worker 持久**——每请求重复追加相同历史，窗口无限重复累积、顺序错乱（实测窗口变 `[hi, hello, hi, hello]`，真实场景下旧消息反复入窗、新消息排不到前面，并伴随多 worker 各自恢复加剧膨胀）

**解决了什么问题**：
- 持久化 store（Redis 生产模式）下短期记忆窗口不再重复累积、顺序保持正确
- 多 uvicorn worker 场景下窗口内容一致（同一 DB 权威快照），避免各 worker 各自膨胀
- 进程内 store（空）仍按原逻辑从 DB 恢复，本地/测试行为不变

---

## 2026-09-02 检索质量改进（词法匹配增强）

**提交**：`d382810`

**改了什么**：
1. `backend/app/memory/retriever.py`：
   - 中文分词从「贪婪两字片段」改为「滑动窗口 bigram」（步长 1）
   - 新增中英文停用词过滤
   - `entity` 字段参与相关性匹配（content 与 entity 取较高者）
2. `backend/tests/test_memory_retrieval_quality.py`：新增检索质量回归测试

**为什么这么改**：
- 原分词 `re.findall(r"[\u4e00-\u9fa5]{2}")` 只取**不重叠**的二字片段，导致 bigram 错位：内容「用户喜欢喝咖啡」只能切出「喝咖」而丢「咖啡」，查询「咖啡」时词法相关度为 0，相关记忆无法被召回
- 查询句中的停用词（the/of/please/这个/想要…）会放大分母，稀释真正关键词的命中率
- 记忆条目的 `entity`（实体字段）此前不参与匹配，按实体提问（如「peter 的偏好」）时即使实体字段命中也无分

**解决了什么问题**：
- 中文错位 bigram 的漏召回（语义相同但写法相邻的记忆）
- 长查询/口语化查询时关键词被停用词稀释导致的排序失真
- 实体维度查询的命中率

---

## 2026-09-02 并发安全整合触发与幂等写入（fbeb1dc）

**提交**：`fbeb1dc`

**改了什么**：
1. `backend/app/memory/persistence.py`：
   - `save_pending_consolidation` 从「整体覆盖」改为**按 (role, content) 去重合并**
   - 新增**模块级共享锁池**（每 session 一个 `asyncio.Lock`，跨实例共享）
   - 新增 `claim_pending_consolidation()`：**原子领取并清空**待整合批次
2. `backend/app/memory/manager.py`：
   - 触发 consolidation 时改为「先合并落库 → 原子领取 → 执行」；失败则**批次回队**
   - `_trigger_consolidation` / `_consolidate_inline` 返回成功状态
3. `backend/app/memory/consolidation.py`：`save_event_entries` 跳过已存在的相同内容条目（幂等）
4. `backend/tests/test_memory_concurrency.py`：新增 6 项并发/幂等回归测试

**为什么这么改**：
- `MemoryPersistence` 每请求重建，旧实现把整个批次从旧快照**覆盖**写回，两个并发请求互相覆盖 → 消息静默丢失（lost update）
- 并发请求各自基于旧快照判断达阈值 → 同一批消息被整合两次 → 重复记忆条目
- 触发前就清空批次，consolidation 失败则消息直接消失

**解决了什么问题**：
- 并发请求下待整合批次不再丢消息（丢失的 pending 会导致记忆永远不沉淀）
- 同一批消息只会被一个请求领取并整合，杜绝重复条目
- 整合失败时批次回队，后续请求自动重试，消息不丢
- 跨进程 / Celery 重试等场景下重复执行也不会产生重复 event 条目

---

## 2026-09-01 跨请求 consolidation 与 e2e 覆盖（16fae24）

**提交**：`16fae24`

**改了什么**：
1. `backend/app/memory/manager.py`：consolidation 批次从「仅进程内存」改为**持久化在会话 `metadata_`**，请求结束前写回、重建时恢复
2. `backend/app/memory/persistence.py`：新增 `load_pending_consolidation` / `save_pending_consolidation`
3. `backend/requirements.txt`：补齐依赖
4. `backend/tests/test_e2e_memory.py`：新增真实 HTTP 端到端回归测试（16 项）

**为什么这么改**：
- 每请求新建 `MemoryManager`，待整合批次只在进程内存中累积；同一会话的跨请求消息永远无法凑满批次阈值 → 记忆在真实 HTTP 场景下从未沉淀
- 曾遇到的根因：bcrypt 版本导致环境启动失败、chromadb 缺失导致向量库初始化失败、SQLAlchemy JSON 列 in-place 修改不触发 dirty → 这些一并修复并纳入 e2e 覆盖

**解决了什么问题**：
- 真实部署（每个请求独立 manager 实例）下，跨请求消息可累积到批次阈值并正常沉淀记忆
- 启动/环境类问题通过 e2e 测试固化，防止回归

---

## 2026-08 数据库方言兼容与迁移优先启动（2356137）

**提交**：`2356137`

**改了什么**：
- 模型列类型改为方言兼容写法（SQLite/PostgreSQL/MySQL 均可建表）
- 启动流程改为「迁移优先」：自动执行 Alembic 迁移后再起服务
- 新增 `backend/tests/test_migrations.py` 迁移回归测试

**为什么这么改**：原模型类型（如 `String` 长度、`DateTime` 时区等）在非 SQLite 方言下无法建表，且启动时依赖手工执行迁移。

**解决了什么问题**：任意支持的数据库方言可直接建表启动，迁移状态可回归验证。

---

## 2026-08 检索规模保护与 event 上限（690a036）

**提交**：`690a036`

**改了什么**：
- `backend/app/memory/retriever.py`：候选集增加规模上限（按强度取前 N，向量命中额外召回）
- `backend/app/memory/consolidation.py`：单会话 event 记忆保留上限（超出归档）
- `backend/app/core/config.py`：新增 `MEMORY_RETRIEVAL_CANDIDATE_LIMIT` / `MEMORY_EVENT_MAX_PER_SESSION`

**为什么这么改**：大量历史记忆时全量打分性能不可控；event 溯源条目无限增长导致检索污染与存储膨胀。

**解决了什么问题**：检索延迟有界，event 条目膨胀可控。

---

## 2026-08 基于 ChromaDB 的向量语义检索（1281418）

**提交**：`1281418`

**改了什么**：
- 新增 `backend/app/memory/vector_store.py`：ChromaDB 记忆向量索引（懒加载、失败自动降级、用户隔离）
- `backend/app/memory/retriever.py`：混合打分加入向量相似度分量（0.3），向量不可用时自动降级

**为什么这么改**：纯词法相关度无法覆盖同义改写/语义近似，需要向量语义检索补齐召回。

**解决了什么问题**：语义相关的记忆（即使无关键词重叠）也能被召回，检索质量显著提升；无向量基础设施时优雅降级，不影响主流程。
