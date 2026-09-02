# Multi-Agent Platform

企业级多Agent协作平台，基于LangGraph工作流引擎，支持智能任务编排、对话管理、RAG检索增强和工具集成。

## 🚀 核心功能

### ✅ 已实现功能

1. **Agent管理系统**
   - 创建、配置和管理多个AI Agent
   - 支持不同类型的Agent（基础Agent、RAG Agent等）
   - Agent执行监控和日志记录

2. **长短期记忆系统**
   - 短期记忆：滑动窗口机制，保留最近N轮对话
   - 长期记忆：LLM驱动的智能摘要
   - 持久化存储：所有对话历史保存到数据库
   - **零语义丢失**：超出窗口的消息自动转移到长期记忆

3. **RAG检索增强生成**
   - 文档处理：支持PDF、TXT、DOCX、MD格式
   - 向量存储：ChromaDB向量数据库
   - 语义检索：基于Embedding的相似性搜索
   - 智能问答：结合检索结果生成准确答案

4. **工作流编排**（v2.0 生产就绪）
   - 基于LangGraph的工作流引擎
   - **LLM智能任务分解** - 动态分析需求生成子任务
   - **真实Agent执行** - 根据任务类型调用对应Agent
   - **结构化代码审查** - 评分系统+问题分类
   - **自动重试机制** - 指数退避策略
   - **实时进度追踪** - 回调函数支持

5. **用户认证与授权**
   - JWT Token认证
   - 角色权限管理
   - API访问控制

### 🔧 技术栈

#### 后端
- **框架**: FastAPI + Python 3.10+
- **数据库**: PostgreSQL (主存储) + Redis (缓存/消息队列)
- **向量数据库**: ChromaDB
- **AI框架**: LangChain + LangGraph
- **认证**: JWT + OAuth2
- **任务队列**: Celery

#### 前端
- **框架**: React 18 + TypeScript
- **UI库**: Ant Design Pro
- **构建工具**: Vite
- **状态管理**: Zustand

## 📋 前置要求

- Docker & Docker Compose
- Python 3.10+
- Node.js 18+

## 🛠️ 快速开始

### 方式一：Docker部署（推荐）

```bash
# 克隆项目
git clone <repository-url>
cd multi-agent

# 复制环境变量配置
cp backend/.env.example backend/.env

# 启动所有服务
docker-compose up -d

# 查看日志
docker-compose logs -f
```

访问以下地址：
- 后端API文档: http://localhost:8000/docs
- 前端应用: http://localhost:3000

### 方式二：本地开发

#### 1. 启动依赖服务

```bash
docker-compose up -d postgres redis chromadb
```

#### 2. 后端设置

```bash
cd backend

# 创建虚拟环境
python -m venv venv
source venv/bin/activate  # Linux/Mac
# 或
venv\Scripts\activate     # Windows

# 安装依赖
pip install -r requirements.txt

# 复制环境变量
cp .env.example .env

# 运行数据库迁移
alembic upgrade head

# 启动开发服务器
uvicorn app.main:app --reload
```

#### 3. 前端设置

```bash
cd frontend

# 安装依赖
npm install

# 启动开发服务器
npm run dev
```

## 📁 项目结构

```
multi-agent/
├── backend/                 # 后端服务
│   ├── app/
│   │   ├── api/            # API路由
│   │   ├── core/           # 核心配置
│   │   ├── models/         # 数据模型
│   │   ├── agents/         # Agent实现
│   │   ├── tools/          # 工具集成
│   │   └── schemas/        # Pydantic schemas
│   ├── requirements.txt
│   └── Dockerfile
├── frontend/               # 前端应用
│   ├── src/
│   ├── package.json
│   └── Dockerfile
├── docker-compose.yml
└── README.md
```

## 🔑 API端点

### 认证
- `POST /api/v1/auth/register` - 用户注册
- `POST /api/v1/auth/login` - 用户登录
- `GET /api/v1/auth/me` - 获取当前用户信息

### Agent管理
- `GET /api/v1/agents` - 列出所有Agent
- `POST /api/v1/agents` - 创建Agent
- `GET /api/v1/agents/{id}` - 获取Agent详情
- `PUT /api/v1/agents/{id}` - 更新Agent
- `DELETE /api/v1/agents/{id}` - 删除Agent
- `POST /api/v1/agents/{id}/execute` - 执行Agent

### 记忆管理
- `POST /api/v1/memory/{session_id}/message` - 添加消息到记忆
- `GET /api/v1/memory/{session_id}/context` - 获取对话上下文
- `GET /api/v1/memory/{session_id}/summary` - 获取长期记忆摘要
- `DELETE /api/v1/memory/{session_id}` - 清空会话记忆

### RAG知识库
- `POST /api/v1/rag/ingest` - 上传文档到知识库
- `POST /api/v1/rag/query` - 查询知识库
- `GET /api/v1/rag/stats/{collection_name}` - 获取知识库统计
- `DELETE /api/v1/rag/clear/{collection_name}` - 清空知识库

### 任务管理
- `GET /api/v1/tasks` - 列出任务
- `POST /api/v1/tasks` - 创建任务
- `GET /api/v1/tasks/{id}` - 获取任务详情
- `PUT /api/v1/tasks/{id}` - 更新任务
- `DELETE /api/v1/tasks/{id}` - 删除任务
- `POST /api/v1/tasks/{id}/cancel` - 取消任务

## 🧪 测试

```bash
# 后端测试
cd backend
pytest

# 前端测试
cd frontend
npm test
```

## 📝 环境变量

参考 `backend/.env.example` 文件配置环境变量：

```bash
# 数据库
DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/multi_agent

# Redis
REDIS_URL=redis://localhost:6379/0

# JWT
SECRET_KEY=your-secret-key-change-in-production

# OpenAI
OPENAI_API_KEY=your-openai-api-key
```

## 📚 文档

### 核心文档
- [记忆系统设计](./docs/memory_system_design.md) - 长短期记忆架构
- [记忆系统使用指南](./docs/MEMORY_USAGE_GUIDE.md) - 如何使用记忆功能
- [RAG系统架构](./docs/RAG_SYSTEM_ARCHITECTURE.md) - RAG技术设计
- [RAG使用指南](./docs/RAG_USAGE_GUIDE.md) - RAG功能使用方法
- [滑动窗口改进说明](./docs/MEMORY_SLIDING_WINDOW_FIX.md) - v2.0改进详解

### 工作流文档（v2.0新增）
- [多Agent协作工作流v2.0](./docs/MULTI_AGENT_WORKFLOW_V2.md) - 完整的技术文档
- [工作流改进总结](./docs/WORKFLOW_IMPROVEMENT_SUMMARY.md) - v1.0到v2.0的改进详情
- [工作流示例代码](./backend/examples/workflow_example.py) - 使用示例

## 🤝 贡献

欢迎提交Issue和Pull Request！

## 📄 许可证

MIT License
