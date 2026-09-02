#!/bin/bash

# API测试脚本
# 用于快速测试后端API功能

BASE_URL="http://localhost:8000/api/v1"

echo "=========================================="
echo "  Multi-Agent Platform - API Test Suite"
echo "=========================================="
echo ""

# 测试1: 健康检查
echo "[Test 1] Health Check..."
curl -s $BASE_URL/../health | python3 -m json.tool || curl -s $BASE_URL/../health
echo ""

# 测试2: 用户注册
echo "[Test 2] User Registration..."
REGISTER_RESPONSE=$(curl -s -X POST "$BASE_URL/auth/register" \
  -H "Content-Type: application/json" \
  -d '{
    "username": "testuser",
    "email": "test@example.com",
    "password": "password123"
  }')

echo $REGISTER_RESPONSE | python3 -m json.tool || echo $REGISTER_RESPONSE
echo ""

# 提取token（如果注册成功）
ACCESS_TOKEN=$(echo $REGISTER_RESPONSE | grep -o '"access_token":"[^"]*"' | cut -d'"' -f4)

if [ -z "$ACCESS_TOKEN" ]; then
    # 尝试登录获取token
    echo "[Test 2b] User Login..."
    LOGIN_RESPONSE=$(curl -s -X POST "$BASE_URL/auth/login" \
      -H "Content-Type: multipart/form-data" \
      -F "username=testuser" \
      -F "password=password123")
    
    echo $LOGIN_RESPONSE | python3 -m json.tool || echo $LOGIN_RESPONSE
    ACCESS_TOKEN=$(echo $LOGIN_RESPONSE | grep -o '"access_token":"[^"]*"' | cut -d'"' -f4)
fi

if [ -n "$ACCESS_TOKEN" ]; then
    echo ""
    echo "✓ Token obtained: ${ACCESS_TOKEN:0:20}..."
else
    echo ""
    echo "✗ Failed to obtain token"
    exit 1
fi

# 测试3: 创建Agent
echo ""
echo "[Test 3] Create Agent..."
AGENT_RESPONSE=$(curl -s -X POST "$BASE_URL/agents" \
  -H "Authorization: Bearer $ACCESS_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Test Coder Agent",
    "type": "coder",
    "description": "A test agent for code generation",
    "capabilities": ["code_generation", "code_review"]
  }')

echo $AGENT_RESPONSE | python3 -m json.tool || echo $AGENT_RESPONSE
echo ""

# 测试4: 列出Agents
echo "[Test 4] List Agents..."
curl -s -X GET "$BASE_URL/agents" \
  -H "Authorization: Bearer $ACCESS_TOKEN" | python3 -m json.tool || \
curl -s -X GET "$BASE_URL/agents" \
  -H "Authorization: Bearer $ACCESS_TOKEN"
echo ""

# 测试5: 创建任务
echo "[Test 5] Create Task..."
TASK_RESPONSE=$(curl -s -X POST "$BASE_URL/tasks" \
  -H "Authorization: Bearer $ACCESS_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "title": "Test Task",
    "description": "A test task",
    "priority": 5
  }')

echo $TASK_RESPONSE | python3 -m json.tool || echo $TASK_RESPONSE
echo ""

# 测试6: 列出任务
echo "[Test 6] List Tasks..."
curl -s -X GET "$BASE_URL/tasks" \
  -H "Authorization: Bearer $ACCESS_TOKEN" | python3 -m json.tool || \
curl -s -X GET "$BASE_URL/tasks" \
  -H "Authorization: Bearer $ACCESS_TOKEN"
echo ""

echo "=========================================="
echo "  API Tests Completed!"
echo "=========================================="
echo ""
echo "Next steps:"
echo "1. Visit http://localhost:8000/docs for full API documentation"
echo "2. Use the access token to test protected endpoints"
echo "3. Create your own agents and tasks"
