# 快速开始指南

## 📦 项目已创建完成

企业级多Agent协作平台的基础架构已经搭建完成！

## 📂 项目结构

```
multi-agent/
├── backend/                    # 后端服务
│   ├── app/
│   │   ├── api/               # API路由 (auth, agents, tasks)
│   │   ├── core/              # 核心配置 (config, database, security, deps)
│   │   ├── models/            # SQLAlchemy数据模型
│   │   ├── schemas/           # Pydantic数据验证
│   │   ├── agents/            # Agent实现 (base, coder, reviewer, registry)
│   │   ├── tools/             # 工具集成 (web_search等)
│   │   └── main.py            # FastAPI应用入口
│   ├── requirements.txt       # Python依赖
│   ├── .env                   # 环境变量配置
│   └── Dockerfile             # Docker镜像
│
├── frontend/                   # 前端应用
│   ├── src/
│   │   ├── components/        # React组件 (Layout)
│   │   ├── pages/             # 页面 (Login, Dashboard, Agents, Tasks, Conversations)
│   │   ├── services/          # API服务
│   │   ├── App.tsx            # 应用主组件
│   │   └── main.tsx           # 入口文件
│   ├── package.json           # Node.js依赖
│   └── Dockerfile             # Docker镜像
│
├── docker-compose.yml         # Docker编排配置
├── README.md                  # 项目文档
└── QUICKSTART.md             # 快速开始指南
```

## 🚀 启动方式

### 方式一：Docker一键启动（推荐）

```bash
# 1. 确保已安装Docker和Docker Compose

# 2. 在项目根目录执行
docker-compose up -d

# 3. 查看服务状态
docker-compose ps

# 4. 查看日志
docker-compose logs -f
```

访问地址：
- 后端API文档: http://localhost:8000/docs
- 前端应用: http://localhost:3000

### 方式二：本地开发模式

#### 1️⃣ 启动依赖服务

```bash
docker-compose up -d postgres redis chromadb
```

#### 2️⃣ 启动后端

```bash
cd backend

# 创建虚拟环境
python -m venv venv

# 激活虚拟环境
# Windows:
venv\Scripts\activate
# Linux/Mac:
source venv/bin/activate

# 安装依赖
pip install -r requirements.txt

# 复制环境变量（如果还没有.env文件）
cp .env.example .env

# 启动开发服务器
uvicorn app.main:app --reload
```

后端将运行在: http://localhost:8000
API文档: http://localhost:8000/docs

#### 3️⃣ 启动前端

```bash
cd frontend

# 安装依赖
npm install

# 启动开发服务器
npm run dev
```

前端将运行在: http://localhost:3000

## ✨ 核心功能

### 后端特性
- ✅ **用户认证系统** - JWT + OAuth2
- ✅ **Agent管理** - 注册、配置、执行
- ✅ **任务编排** - 创建、跟踪、取消任务
- ✅ **对话管理** - 多轮对话、上下文维护
- ✅ **工具集成** - Web搜索、代码执行等
- ✅ **异步支持** - FastAPI + asyncio
- ✅ **数据库** - PostgreSQL + Redis
- ✅ **向量检索** - ChromaDB集成

### 前端特性
- ✅ **响应式布局** - Ant Design Pro
- ✅ **路由管理** - React Router v6
- ✅ **状态管理** - TanStack Query
- ✅ **类型安全** - TypeScript
- ✅ **现代化UI** - TailwindCSS
- ✅ **页面组件**:
  - 登录页面
  - 仪表盘
  - Agent管理
  - 任务管理
  - 对话管理

## 🔑 默认配置

### 环境变量
参考 `backend/.env` 文件，关键配置：
- 数据库连接
- JWT密钥
- OpenAI API密钥（可选）
- Redis连接

### 测试账号
首次启动需要注册新用户，或通过API创建：

```bash
curl -X POST http://localhost:8000/api/v1/auth/register \
  -H "Content-Type: application/json" \
  -d '{
    "username": "admin",
    "email": "admin@example.com",
    "password": "admin123"
  }'
```

## 🧪 测试API

### 1. 注册用户
```bash
curl -X POST http://localhost:8000/api/v1/auth/register \
  -H "Content-Type: application/json" \
  -d '{"username": "testuser", "email": "test@example.com", "password": "password123"}'
```

### 2. 登录获取Token
```bash
curl -X POST http://localhost:8000/api/v1/auth/login \
  -H "Content-Type: multipart/form-data" \
  -F "username=testuser" \
  -F "password=password123"
```

### 3. 创建Agent
```bash
curl -X POST http://localhost:8000/api/v1/agents \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Test Coder",
    "type": "coder",
    "description": "Test agent",
    "capabilities": ["code_generation"]
  }'
```

## 📝 下一步

1. **配置OpenAI API密钥** - 在 `.env` 文件中设置 `OPENAI_API_KEY`
2. **创建自定义Agent** - 继承 `BaseAgent` 类实现自己的Agent
3. **添加工具** - 实现 `BaseTool` 接口扩展功能
4. **完善前端** - 连接真实API，替换Mock数据
5. **添加测试** - 编写单元测试和集成测试

## 🛠️ 常用命令

### Docker
```bash
# 启动所有服务
docker-compose up -d

# 停止所有服务
docker-compose down

# 查看日志
docker-compose logs -f [service_name]

# 重启服务
docker-compose restart [service_name]

# 进入容器
docker-compose exec backend bash
```

### 后端
```bash
# 安装依赖
pip install -r requirements.txt

# 运行开发服务器
uvicorn app.main:app --reload

# 运行测试
pytest

# 数据库迁移
alembic upgrade head
```

### 前端
```bash
# 安装依赖
npm install

# 开发模式
npm run dev

# 构建生产版本
npm run build

# 代码检查
npm run lint
```

## 🐛 故障排查

### 后端无法连接数据库
```bash
# 检查PostgreSQL是否运行
docker-compose ps postgres

# 查看日志
docker-compose logs postgres
```

### 前端无法连接后端
- 确认后端运行在 http://localhost:8000
- 检查 `frontend/vite.config.ts` 中的代理配置
- 查看浏览器控制台的网络请求

### Docker容器启动失败
```bash
# 查看详细日志
docker-compose logs

# 重新构建镜像
docker-compose build --no-cache
```

## 📚 技术栈说明

### 后端
- **FastAPI** - 高性能异步Web框架
- **SQLAlchemy** - ORM数据库操作
- **LangChain/LangGraph** - AI Agent框架
- **JWT** - 身份认证
- **Celery** - 异步任务队列

### 前端
- **React 18** - UI框架
- **TypeScript** - 类型安全
- **Ant Design** - UI组件库
- **TanStack Query** - 数据获取和缓存
- **Vite** - 快速构建工具

## 🎯 项目特点

1. **模块化设计** - 清晰的分层架构
2. **类型安全** - 前后端完整的类型定义
3. **可扩展性** - 插件化Agent和工具系统
4. **生产就绪** - Docker容器化部署
5. **开发友好** - 热重载、详细文档
6. **安全性** - JWT认证、密码加密、CORS保护

## 💡 提示

- 开发时建议使用 `--reload` 参数自动重载代码
- 生产环境务必修改 `SECRET_KEY`
- 定期备份数据库数据
- 使用 `.env` 文件管理敏感配置

---

**祝您使用愉快！** 🎉

如有问题，请查看 README.md 或提交 Issue。
