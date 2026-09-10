# 安全债台账（SECURITY-DEBT）

> 建立日期：2026-09-10
> 关联文件：`security/pip-audit-baseline.txt`、`security/audit-ci.json`、`.github/workflows/ci.yml`

## 1. 这份文档解决什么问题

上一轮引入远程 CI 后，`Security & Dependency Scan` 首次真实运行就暴露出一批
**在门禁建立之前就已存在**的依赖漏洞：

| 扫描 | 命令 | 建立门禁时的结果 |
| --- | --- | --- |
| 后端 | `pip-audit -r backend/requirements.lock` | 49 项 / 14 个包 |
| 前端 | `npm audit --audit-level=high` | 17 项 / 14 条 advisory（含 2 critical） |

这些漏洞**不是本次改动引入的**，属于历史欠账。但它们已经真实地卡住了 CI。

处理策略：**先豁免、后升级**。

- 把当前全部欠账固化为"基线"，让门禁对**新增**漏洞保持拦截力；
- 基线只允许缩小，不允许扩大；
- 分阶段升级，每完成一批就删除对应基线条目。

选择这个策略的前提是先确认了一件事：**这道门禁无法仅靠升级变绿**。
`chromadb 1.5.9`（4 项）与 `ecdsa 0.19.2`（1 项）在上游**没有修复版本**，
无论怎么升级都会持续命中。也就是说豁免机制是不可避免的，问题只在于
"豁免哪些、豁免到什么程度"。既然必须有基线，就应该把基线做成显式、可审计、
可收敛的资产，而不是靠放宽门禁阈值把问题藏起来。

## 2. 门禁语义（重要）

豁免 ≠ 关掉门禁。基线机制下的判定规则是：

- 漏洞 ID 在基线内 → 忽略，CI 通过；
- 漏洞 ID **不在**基线内 → CI 红灯，阻止合并。

也就是说：现有欠账被冻结，但**任何新引入的漏洞都会被拦下**。
这与"把 job 改成 `continue-on-error`"有本质区别——后者会让门禁彻底失去拦截力。

## 3. 机制如何工作

### 后端

`pip-audit` 没有原生的基线文件支持，但提供了可重复的 `--ignore-vuln <ID>` 参数。
CI 从 `security/pip-audit-baseline.txt` 读取 ID 列表并展开为忽略参数：

```bash
BASELINE=security/pip-audit-baseline.txt
IGNORES=$(grep -vE '^[[:space:]]*(#|$)' "$BASELINE" \
  | awk '{print $1}' | sed 's/^/--ignore-vuln /' | tr '\n' ' ')
pip-audit -r backend/requirements.lock $IGNORES
```

基线文件里 `#` 之后是给人看的说明，读取时被忽略，因此维护成本很低。

### 前端

`npm audit` 不支持按 advisory 排除，因此改用 `audit-ci`（CI 专用工具，
支持 advisory 级 allowlist，并按严重度阈值决定退出码），配置见
`security/audit-ci.json`：

```bash
npx --yes audit-ci@7.1.0 --config ../security/audit-ci.json --high
```

`--high` 与原先的 `npm audit --audit-level=high` 语义一致：
high 及以上未豁免即红灯，moderate 不拦截。

注意 `audit-ci` v7 起 allowlist **只接受 GitHub advisory 标识（`GHSA-...`）**，
不再接受数字形式的 advisory ID（传数字会直接抛 `Unsupported number as allowlist`）。
因此配置文件与下表统一使用 GHSA 编码。

## 4. 后端基线明细（49 项）

完整 ID 清单见 `security/pip-audit-baseline.txt`，这里按处理批次归类。

| 组 | 包 | 项数 | 处理方式 |
| --- | --- | --- | --- |
| A | `chromadb 1.5.9` | 4 | **无修复版本**，只能跟踪上游 |
| A | `ecdsa 0.19.2` | 1 | **无修复版本**；可改用 `python-jose[cryptography]` 绕开 |
| B | `langchain` 全家桶 | 24 | 需 0.1.x → 0.3.x/1.x 破坏性迁移，独立批次 |
| C | `fastapi 0.109.0` + `starlette 0.35.1` | 8 | FastAPI 钉死 starlette <0.36，必须同步升级 |
| D | `python-multipart` / `python-dotenv` / `python-jose` | 12 | 低风险，可单独升级，建议第一批清理 |

组 B 明细：`langchain`(6)、`langchain-community`(4)、`langchain-core`(6)、
`langchain-text-splitters`(2)、`langsmith`(3)、`langchain-openai`(1)、`langgraph`(2)。

## 5. 前端基线明细（14 条 advisory）

| GitHub advisory | 包 | 严重度 | 说明 | 解除方式 |
| --- | --- | --- | --- | --- |
| `GHSA-5xrq-8626-4rwp` | `vitest` | critical | Vitest UI server 任意文件读取/执行 | 随 vite/vitest 升级 |
| `GHSA-fx2h-pf6j-xcff` | `vite` | high | `server.fs.deny` 在 Windows 下可绕过 | 随 vite 升级 |
| `GHSA-j3q9-mxjg-w52f` | `path-to-regexp` | high | ReDoS（顺序可选组） | 经 `@ant-design/pro-layout` 传入，需换依赖版本 |
| `GHSA-27v5-c462-wpq7` | `path-to-regexp` | moderate | ReDoS（多通配符） | 同上 |
| `GHSA-2883-xcg3-v3hh` | `js-yaml` | high | `maxTotalMergeKeys` 未限制 CPU | 传递依赖，随上游升级 |
| `GHSA-3ppc-4f35-3m26` | `minimatch` | high | ReDoS（重复通配符） | 随 `@typescript-eslint` 6→7 |
| `GHSA-7r86-cg39-jmmj` | `minimatch` | high | ReDoS（GLOBSTAR 回溯） | 同上 |
| `GHSA-23c5-xmqv-rm74` | `minimatch` | high | ReDoS（嵌套 extglob） | 同上 |
| `GHSA-wrjc-x8rr-h8h6` | `react-router` | moderate | Open redirect | 需 `react-router-dom` 6→7 |
| `GHSA-337j-9hxr-rhxg` | `react-router` | moderate | SSR 水合期构造器注入 | 同上 |
| `GHSA-jjmj-jmhj-qwj2` | `react-router-dom` | moderate | Open redirect 导致 XSS | 同上 |
| `GHSA-4w7w-66w2-5vf9` | `vite` | moderate | 优化依赖 `.map` 路径穿越 | 随 vite 升级 |
| `GHSA-v6wh-96g9-6wx3` | `vite` | moderate | `launch-editor` NTLMv2 哈希泄露 | 随 vite 升级 |
| `GHSA-67mh-4wv8-2f99` | `esbuild` | moderate | 开发服务器可被任意站点访问 | 需 vite 8（破坏性） |

**关于实际暴露面**：`vite` / `vitest` / `esbuild` 这几条的触发前提是"开发服务器
对外可达"。生产镜像里前端只跑 Nginx 托管的静态产物，不含 dev server，
因此这几条在**当前部署形态下不构成生产暴露**——这也是它们被放进第一批豁免
而不是立即处理的原因。`react-router` 与 `path-to-regexp` 属于运行时依赖，
需要在升级批次里真实修掉，不能长期豁免。

## 6. 分阶段升级计划

| 批次 | 范围 | 预计解除 | 风险 | 前置条件 |
| --- | --- | --- | --- | --- |
| 第一 | `python-multipart`、`python-dotenv`、`python-jose` | 12 项 | 低 | 跑 27 套件门禁 |
| 第二 | FastAPI + starlette 同步升级 | 8 项 | 中 | 需回归中间件/路由/依赖注入相关行为 |
| 第三 | 前端 `@typescript-eslint` 6→7、`js-yaml`、`path-to-regexp` | 5 条 | 中 | lint 与 build 回归 |
| 第四 | `react-router-dom` 6→7、`vite` 5→8、`vitest` | 5 条 | 高 | 路由与构建链路改动，需完整前端回归 |
| 第五 | langchain 0.1 → 0.3/1.x 全家桶 | 24 项 | 高 | RAG 链路改造，需专项验证 |
| 待上游 | `chromadb` 4 项、`ecdsa` 1 项 | 5 项 | — | `ecdsa` 可先切 `cryptography` 后端解除 |

建议顺序即上表顺序：先做低风险换取基线的实质缩小，把大迁移推后进行。

## 7. Bandit 豁免记录

CI 的 bandit 步骤（`bandit -r backend/app -ll`）当前为全绿。其中有 **1 处显式豁免**
需要在此留档，避免后来者误解为遗漏：

| 位置 | 规则 | 理由 |
| --- | --- | --- |
| `backend/app/main.py` — `uvicorn.run(host="0.0.0.0")` | B104（hardcoded_bind_all_interfaces） | 容器内必须监听所有接口，否则无法被 Nginx / 同网络内的其他容器访问。**外部暴露面不由应用侧收敛**，而由编排层控制：`compose.prod.yml` 中只有前端 8080 对外发布，后端端口仅绑定回环地址 |

该豁免以 `# nosec B104` 标注，理由另起一行写成普通注释（而不是写在 `# nosec` 后面，
否则 bandit 会把行内注释当成测试名解析并产生 warning）。

## 8. gitleaks 误报与配置豁免

### 背景

CI 第二个 run（`de501eb`）的失败点不是依赖扫描，而是 gitleaks 报 `Leaks detected`。
按 CI 实际使用的版本（8.30.1）本地复现后，确认是 **13 条误报**，全部来自文档示例与测试夹具：

| 规则 | 数量 | 位置 | 性质 |
| --- | --- | --- | --- |
| `curl-auth-header` | 12 | `QUICKSTART.md`、`WORKFLOW_IMPLEMENTATION.md`、`docs/RAG_USAGE_GUIDE.md`、`docs/RAG_IMPLEMENTATION_SUMMARY.md`、`docs/WORKFLOW_V2_COMPLETION_REPORT.md` | curl 示例中的 `Authorization: Bearer YOUR_TOKEN` |
| `generic-api-key` | 1 | `backend/tests/pytest_suite/conftest.py:19` | 测试夹具的固定假 `SECRET_KEY` |

定位过程中有两个值得记住的结论：

1. **规则集版本会改变结论**。本机原有的 gitleaks 8.18.4 扫全量历史是 0 命中，
   换到 8.30.1 才报出这 13 条 —— `curl-auth-header` 是较新版本加入的规则。
   所以"本地扫过没问题"不能替代"用 CI 的实际版本扫一遍"。
2. **浅克隆会改变扫描范围**。security 任务的 `actions/checkout` 缺 `fetch-depth: 0`，
   浅克隆下 gitleaks-action 退化为"整个项目快照"扫描。
   实测本次推送范围（`a71235c..de501eb`）本身 0 命中，CI 却报 leaks，正是这个原因。

### 处理方式

1. **修内容**：12 处 curl 示例统一改为 `Authorization: Bearer <YOUR_TOKEN>`，
   与仓库已有的 `Bearer <token>` 写法一致；`conftest.py` 的假密钥保留原值
   （测试需要足够熵的值），改用官方行内机制 `# gitleaks:allow` 放行。

2. **加配置**（`/.gitleaks.toml`）：已提交的旧 blob 里仍有 `Bearer YOUR_TOKEN`，
   改文件无法消除它们，必须靠配置兜住。配置刻意做成**窄口径**：

   | 项 | 取值 | 设计意图 |
   | --- | --- | --- |
   | `[extend] useDefault` | `true` | 继承官方全部规则，不放宽任何规则 |
   | `regexTarget` | `line` | 按行内容匹配，而不是按路径整体放行 |
   | `regexes` | 3 条字面占位符 | 只放行 `YOUR_TOKEN` / `sk-your-key-here` / 该测试假密钥 |

   真实凭据不会包含这些占位符字面量，因此放行范围是可控的。

3. **补 `fetch-depth: 0`**：让 gitleaks-action 能按 push 范围精确扫描，
   而不是退化为全量快照扫描。

### 实证

| 场景 | 结果 |
| --- | --- |
| 修复前，8.30.1 全量历史 | `exit 1`，13 条命中 |
| 修复后，全量历史 / 工作树 | 均 `exit 0`，非 vendor 命中 0 |
| 反向对照：真实形态的高熵密钥 | 仍被 `generic-api-key` 捕获（证明扫描器没变成摆设） |
| 反向对照：`Bearer <YOUR_TOKEN>` 与 `sk-your-key-here` | 0 命中（证明放行是窄口径的） |

## 9. 复核节奏与退出条件

- **节奏**：每季度复核一次；任何依赖升级完成后立即复核并删除已解除条目。
- **新增豁免门槛**：默认禁止。只有"上游确实没有修复版本"才允许新增，
  且必须在本文档登记理由与跟踪方式。
- **退出条件**：本台账清空（`security/pip-audit-baseline.txt` 与
  `security/audit-ci.json` 的 `allowlist` 均为空）时，删除本节第 1 节所述的
  过渡机制，恢复为纯门禁。**基线为空即为目标态**。
- **gitleaks 配置的退出条件**：当仓库内不再需要放行任何占位符
  （即 `.gitleaks.toml` 的 `regexes` 可清空）时，删除该配置文件，回到纯默认规则集。
