# 本地开发环境配置指南

## 📋 必需软件清单

### 1. Python 3.10+ ⭐⭐⭐⭐⭐
**用途**: 后端FastAPI应用开发

**下载地址**: https://www.python.org/downloads/windows/

**安装步骤**:
1. 下载 Python 3.10 或更高版本（推荐 3.10-3.12）
2. 运行安装程序
3. ⚠️ **重要**: 勾选 "Add Python to PATH"
4. 选择 "Install Now" 或自定义安装路径
5. 等待安装完成

**验证安装**:
```powershell
python --version
pip --version
```

---

### 2. Node.js 18+ (LTS) ⭐⭐⭐⭐⭐
**用途**: 前端React应用开发

**下载地址**: https://nodejs.org/

**安装步骤**:
1. 下载 LTS (Long Term Support) 版本
2. 运行安装程序
3. 使用默认选项即可
4. 等待安装完成

**验证安装**:
```powershell
node --version
npm --version
```

---

### 3. PostgreSQL 15 (可选) ⭐⭐⭐
**用途**: 数据库存储

**选项A - 使用Docker（推荐）**:
```powershell
docker run -d --name postgres \
  -e POSTGRES_DB=multi_agent \
  -e POSTGRES_USER=postgres \
  -e POSTGRES_PASSWORD=postgres \
  -p 5432:5432 \
  postgres:15-alpine
```

**选项B - 本地安装**:
- 下载: https://www.postgresql.org/download/windows/
- 安装时记住设置的密码
- 创建数据库 `multi_agent`

---

### 4. Redis 7 (可选) ⭐⭐⭐
**用途**: 缓存和消息队列

**选项A - 使用Docker（推荐）**:
```powershell
docker run -d --name redis \
  -p 6379:6379 \
  redis:7-alpine
```

**选项B - Windows版**:
- 下载: https://github.com/microsoftarchive/redis/releases
- 解压后运行 `redis-server.exe`

---

## 🚀 快速安装方案

### 方案一：使用 Winget（Windows包管理器）

如果您的Windows 11支持winget，可以一键安装：

```powershell
# 安装 Python
winget install Python.Python.3.11

# 安装 Node.js
winget install OpenJS.NodeJS.LTS

# 验证
python --version
node --version
```

### 方案二：手动下载安装

1. **Python**: https://www.python.org/downloads/
   - 下载 Windows installer (64-bit)
   - 运行安装，勾选 "Add to PATH"

2. **Node.js**: https://nodejs.org/
   - 下载 Windows Installer (.msi)
   - 运行安装，使用默认选项

---

## ⚙️ 安装后配置

### 1. 配置Python虚拟环境

```powershell
cd D:\multi-agent\backend

# 创建虚拟环境
python -m venv venv

# 激活虚拟环境
.\venv\Scripts\Activate.ps1

# 如果提示执行策略错误，运行：
Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned -Force
```

### 2. 安装Python依赖

```powershell
# 确保虚拟环境已激活
# 命令行前面应该显示 (venv)

pip install --upgrade pip
pip install -r requirements.txt
```

### 3. 配置环境变量

```powershell
cd D:\multi-agent\backend

# 复制环境变量模板
Copy-Item .env.example .env

# 编辑 .env 文件，配置数据库连接等
notepad .env
```

### 4. 安装前端依赖

打开新的PowerShell窗口：

```powershell
cd D:\multi-agent\frontend

# 安装依赖
npm install

# 如果使用国内网络，可以使用淘宝镜像
npm config set registry https://registry.npmmirror.com
npm install
```

---

## 🔧 常见问题解决

### 问题1: PowerShell执行策略限制

**错误信息**: 
```
无法加载文件 xxx.ps1，因为在此系统上禁止运行脚本
```

**解决方案**:
```powershell
Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned -Force
```

### 问题2: Python未添加到PATH

**症状**: 运行 `python` 提示找不到命令

**解决方案**:
1. 重新运行Python安装程序
2. 选择 "Modify"
3. 勾选 "Add Python to PATH"
4. 完成安装

或者手动添加：
1. 右键"此电脑" → 属性 → 高级系统设置
2. 环境变量 → Path → 编辑
3. 添加Python安装路径，如：`C:\Python311\`
4. 添加Scripts路径：`C:\Python311\Scripts\`

### 问题3: npm安装依赖失败

**解决方案**:
```powershell
# 清除缓存
npm cache clean --force

# 使用淘宝镜像
npm config set registry https://registry.npmmirror.com

# 重新安装
npm install
```

### 问题4: 端口被占用

**检查端口**:
```powershell
# 检查8000端口
netstat -ano | findstr :8000

# 检查3000端口
netstat -ano | findstr :3000
```

**解决方案**: 修改配置文件中的端口号

---

## ✅ 验证安装

运行环境检查脚本：

```powershell
cd D:\multi-agent
.\check_env.ps1
```

应该看到所有检查项都通过 ✓

---

## 🎯 下一步

环境配置完成后：

1. **启动依赖服务** (PostgreSQL, Redis)
   ```powershell
   # 如果安装了Docker
   docker compose up -d postgres redis chromadb
   ```

2. **启动后端**
   ```powershell
   cd backend
   .\venv\Scripts\Activate.ps1
   uvicorn app.main:app --reload
   ```

3. **启动前端** (新窗口)
   ```powershell
   cd frontend
   npm run dev
   ```

4. **访问应用**
   - 前端: http://localhost:3000
   - API文档: http://localhost:8000/docs

---

## 📞 需要帮助？

如果遇到问题：
1. 查看 QUICKSTART.md 详细指南
2. 检查 README.md 项目说明
3. 查看官方文档链接

祝您配置顺利！🚀
