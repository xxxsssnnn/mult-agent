# 系统架构说明

## 整体架构

```
┌─────────────────────────────────────────────────────────────┐
│                      Client Layer                           │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐                 │
│  │   Web    │  │  Mobile  │  │   API    │                 │
│  │ Browser  │  │   App    │  │ Clients  │                 │
│  └────┬─────┘  └────┬─────┘  └────┬─────┘                 │
└───────┼─────────────┼─────────────┼────────────────────────┘
        │             │             │
        └─────────────┴─────────────┘
                      │ HTTPS/WSS
        ┌─────────────┴─────────────┐
        │     Load Balancer         │
        │      (Nginx)              │
        └─────────────┬─────────────┘
                      │
┌─────────────────────┼─────────────────────────────────────┐
│                Presentation Layer                          │
│  ┌──────────────────────────────────────────────────┐     │
│  │         React Frontend (Port 3000)               │     │
│  │  - Ant Design UI                                 │     │
│  │  - React Router                                  │     │
│  │  - TanStack Query                                │     │
│  └──────────────────────────────────────────────────┘     │
└─────────────────────┬─────────────────────────────────────┘
                      │ HTTP/WebSocket
┌─────────────────────┴─────────────────────────────────────┐
│                    API Gateway Layer                       │
│  ┌──────────────────────────────────────────────────┐     │
│  │       FastAPI Backend (Port 8000)                │     │
│  │  - Authentication Middleware                     │     │
│  │  - CORS Protection                               │     │
│  │  - Rate Limiting                                 │     │
│  │  - Request Validation                            │     │
│  └──────────────────────────────────────────────────┘     │
└────────┬──────────────────┬────────────────┬──────────────┘
         │                  │                │
         │                  │                │
┌────────┴──────┐  ┌───────┴──────┐  ┌─────┴────────┐
│  Auth Module  │  │ Agent Module │  │ Task Module  │
│               │  │              │  │              │
│ - Register    │  │ - Registry   │  │ - Create     │
│ - Login       │  │ - Execute    │  │ - Track      │
│ - JWT Token   │  │ - Health     │  │ - Cancel     │
│ - RBAC        │  │ - Discovery  │  │ - Retry      │
└───────────────┘  └──────────────┘  └──────────────┘
                                              │
┌─────────────────────────────────────────────┼──────────┐
│              Business Logic Layer           │          │
│  ┌──────────────────────────────────────────┴───────┐  │
│  │          Agent Engine                            │  │
│  │  ┌────────────┐  ┌────────────┐  ┌───────────┐ │  │
│  │  │ CoderAgent │  │ReviewerAgnt│  │Custom Agt │ │  │
│  │  └────────────┘  └────────────┘  └───────────┘ │  │
│  │                                                  │  │
│  │  - LangChain Integration                        │  │
│  │  - LangGraph Workflows                          │  │
│  │  - Tool Orchestration                           │  │
│  └──────────────────────────────────────────────────┘  │
│  ┌──────────────────────────────────────────────────┐  │
│  │           Tool System                            │  │
│  │  - Web Search  - Code Executor                   │  │
│  │  - File Ops    - API Caller                      │  │
│  └──────────────────────────────────────────────────┘  │
└────────────────────────┬────────────────────────────────┘
                         │
┌────────────────────────┴────────────────────────────────┐
│                   Data Access Layer                      │
│  ┌──────────────┐  ┌──────────┐  ┌──────────────────┐  │
│  │ PostgreSQL   │  │  Redis   │  │   ChromaDB       │  │
│  │              │  │          │  │                  │  │
│  │ - Users      │  │ - Cache  │  │ - Embeddings     │  │
│  │ - Agents     │  │ - Queue  │  │ - Vector Store   │  │
│  │ - Tasks      │  │ - Pub/Sub│  │ - RAG            │  │
│  │ - Messages   │  │          │  │                  │  │
│  └──────────────┘  └──────────┘  └──────────────────┘  │
└─────────────────────────────────────────────────────────┘
```

## 数据流示例：Agent任务执行

```
User Request
    │
    ▼
┌─────────────────┐
│  React Frontend │
└────────┬────────┘
         │ POST /api/v1/tasks
         ▼
┌─────────────────┐
│  Auth Check     │ ← JWT Validation
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  Task Service   │ → Create Task Record in DB
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ Agent Orchestrator │
│  (LangGraph)     │
└────────┬────────┘
         │
    ┌────┴────┐
    │         │
    ▼         ▼
┌────────┐ ┌──────────┐
│ Coder  │ │ Reviewer │
│ Agent  │ │ Agent    │
└───┬────┘ └────┬─────┘
    │           │
    │  ┌────────▼────────┐
    │  │   LLM (OpenAI)  │
    │  └────────┬────────┘
    │           │
    └────┬──────┘
         │
         ▼
┌─────────────────┐
│  Update Task    │ → Save Results to DB
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ WebSocket Push  │ → Real-time Update
└────────┬────────┘
         │
         ▼
   User Receives Result
```

## 技术栈分层

### Frontend Stack
```
React 18
├── UI Components: Ant Design
├── State Management: TanStack Query + Zustand
├── Routing: React Router v6
├── HTTP Client: Axios
├── Styling: TailwindCSS
└── Build Tool: Vite
```

### Backend Stack
```
FastAPI
├── Database ORM: SQLAlchemy Async
├── Authentication: JWT + OAuth2
├── Validation: Pydantic
├── AI Framework: LangChain + LangGraph
├── Task Queue: Celery
├── Logging: structlog
└── ASGI Server: Uvicorn
```

### Infrastructure Stack
```
Docker Compose
├── Database: PostgreSQL 15
├── Cache: Redis 7
├── Vector DB: ChromaDB
├── Backend: Python 3.10
├── Frontend: Nginx
└── Worker: Celery
```

## 安全架构

```
┌────────────────────────────────────────┐
│         Security Layers                │
│                                        │
│  1. Network Level                      │
│     - HTTPS/TLS                        │
│     - CORS Policy                      │
│     - Rate Limiting                    │
│                                        │
│  2. Application Level                  │
│     - JWT Authentication               │
│     - Password Hashing (bcrypt)        │
│     - Input Validation (Pydantic)      │
│     - SQL Injection Prevention         │
│                                        │
│  3. Data Level                         │
│     - Encrypted Secrets                │
│     - Secure Session Management        │
│     - Audit Logging                    │
│                                        │
│  4. Agent Sandbox                      │
│     - Docker Isolation                 │
│     - Resource Quotas                  │
│     - Network Restrictions             │
└────────────────────────────────────────┘
```

## 扩展性设计

### 水平扩展
- 无状态API服务 → 可多实例部署
- Redis共享Session → 支持负载均衡
- 数据库连接池 → 高并发支持

### 垂直扩展
- 插件化Agent系统 → 轻松添加新Agent
- 工具注册机制 → 动态扩展能力
- 配置驱动 → 无需修改代码

### 微服务就绪
当前为模块化单体，可轻松拆分为：
- Auth Service
- Agent Service
- Task Service
- Conversation Service

## 性能优化

### 后端优化
- 异步I/O (async/await)
- 数据库连接池
- Redis缓存热点数据
- 懒加载关系查询

### 前端优化
- 代码分割 (Code Splitting)
- 懒加载路由
- React Query缓存
- Tree Shaking

### 网络优化
- Gzip压缩
- CDN静态资源
- HTTP/2支持
- Keep-Alive连接

## 监控与日志

```
Application Logs (structlog)
    ↓
Log Aggregator (ELK Stack)
    ↓
Metrics (Prometheus)
    ↓
Visualization (Grafana)
    ↓
Alerts (Alertmanager)
```

## 部署拓扑

### Development
```
Local Machine
├── Docker Containers
│   ├── PostgreSQL
│   ├── Redis
│   ├── ChromaDB
│   └── Backend + Frontend
```

### Production
```
Cloud Provider (AWS/GCP/Azure)
├── Load Balancer
├── Multiple API Instances
├── Managed Database (RDS)
├── Managed Redis (ElastiCache)
├── Celery Workers (Auto-scaling)
└── CDN for Static Assets
```

---

**架构特点总结：**

1. **分层清晰** - 表现层、API层、业务层、数据层
2. **松耦合** - 模块化设计，易于维护和扩展
3. **高可用** - 容器化部署，支持水平扩展
4. **安全性** - 多层安全防护
5. **现代化** - 采用最新技术栈和最佳实践
