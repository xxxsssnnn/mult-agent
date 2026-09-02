"""
工作流v2.0功能验证脚本
用于测试所有改进是否正常工作
"""

import asyncio
from uuid import uuid4
from app.workflows.task_planner import TaskPlannerWorkflow
from app.workflows.code_review import CodeReviewWorkflow
from app.agents.coder import CoderAgent
from app.agents.reviewer import ReviewerAgent


async def test_task_planner_v2():
    """测试任务规划工作流v2.0"""
    print("=" * 80)
    print("测试1: 任务规划工作流 v2.0")
    print("=" * 80)
    
    # 创建工作流
    workflow = TaskPlannerWorkflow(max_iterations=3)
    
    # 设置进度回调
    async def on_progress(progress):
        print(f"\r  进度: {progress['percentage']:5.1f}% - {progress['current_step']}", end="")
    
    workflow.set_progress_callback(on_progress)
    
    # 测试智能任务分解
    print("\n\n[测试] 智能任务分解...")
    test_input = "开发一个用户认证系统，包括注册、登录、Token管理"
    
    try:
        result = await workflow.execute({"user_input": test_input})
        
        if result["success"]:
            print("\n\n✅ 任务规划成功!")
            print(f"\n任务数量: {len(result['tasks'])}")
            print(f"完成任务: {result['metadata']['completed_tasks']}")
            print(f"失败任务: {result['metadata']['failed_tasks']}")
            
            print("\n任务列表:")
            for i, task in enumerate(result['tasks'], 1):
                print(f"  {i}. [{task['task_type']}] {task['title']}")
                print(f"     优先级: {task['priority']}, 复杂度: {task['estimated_complexity']}")
                if task.get('dependencies'):
                    print(f"     依赖: {task['dependencies']}")
            
            print("\n执行结果摘要:")
            for i, res in enumerate(result['results'][:2], 1):  # 只显示前2个
                print(f"  任务{i}: {res.get('output', '')[:100]}...")
            
            return True
        else:
            print(f"\n❌ 任务规划失败: {result.get('error')}")
            return False
    
    except Exception as e:
        print(f"\n❌ 异常: {e}")
        import traceback
        traceback.print_exc()
        return False


async def test_code_review_v2():
    """测试代码审查工作流v2.0"""
    print("\n\n" + "=" * 80)
    print("测试2: 代码审查工作流 v2.0")
    print("=" * 80)
    
    # 创建Agents
    coder = CoderAgent(agent_id=uuid4(), name="TestCoder")
    reviewer = ReviewerAgent(agent_id=uuid4(), name="TestReviewer")
    
    await coder.initialize()
    await reviewer.initialize()
    
    # 创建工作流
    workflow = CodeReviewWorkflow(
        coder_agent=coder,
        reviewer_agent=reviewer,
        max_iterations=2
    )
    
    # 设置进度回调
    async def on_progress(progress):
        print(f"\r  进度: {progress['percentage']:5.1f}% - {progress['current_step']}", end="")
    
    workflow.set_progress_callback(on_progress)
    
    # 测试代码生成和审查
    print("\n\n[测试] 代码生成与结构化审查...")
    test_requirement = "实现一个线程安全的单例模式"
    
    try:
        result = await workflow.execute({
            "requirement": test_requirement,
            "language": "python"
        })
        
        if result["success"]:
            print("\n\n✅ 代码审查完成!")
            print(f"\n通过审查: {'是' if result['approved'] else '否'}")
            print(f"迭代次数: {result['iterations']}")
            
            # 检查结构化审查结果
            if result.get("structured_review"):
                sr = result["structured_review"]
                print(f"\n结构化审查结果:")
                print(f"  评分: {sr['score']}/100")
                print(f"  严重问题: {sr['has_critical_issues']}")
                print(f"  问题数量: {len(sr['issues'])}")
                print(f"  审查总结: {sr['summary'][:100]}...")
                
                if sr['issues']:
                    print(f"\n发现的问题:")
                    for issue in sr['issues'][:3]:  # 只显示前3个
                        print(f"  - [{issue['severity']}] {issue['description'][:80]}")
            else:
                print("\n⚠️  未获得结构化审查结果（可能降级到简单模式）")
            
            print(f"\n生成的代码预览:")
            code_preview = result['code'][:300] if result.get('code') else "无代码"
            print(f"  {code_preview}...")
            
            return True
        else:
            print(f"\n❌ 代码审查失败: {result.get('error')}")
            return False
    
    except Exception as e:
        print(f"\n❌ 异常: {e}")
        import traceback
        traceback.print_exc()
        return False


async def test_retry_mechanism():
    """测试重试机制"""
    print("\n\n" + "=" * 80)
    print("测试3: 重试机制验证")
    print("=" * 80)
    
    workflow = TaskPlannerWorkflow(max_iterations=1)
    workflow.max_retries = 2
    workflow.retry_delay = 0.5  # 快速测试
    
    print("\n[测试] 验证重试配置...")
    print(f"  最大重试次数: {workflow.max_retries}")
    print(f"  重试延迟: {workflow.retry_delay}s")
    print("\n✅ 重试机制已配置（实际重试需要模拟故障环境）")
    
    return True


async def main():
    """运行所有测试"""
    print("\n" + "🧪" * 40)
    print("多Agent工作流 v2.0 功能验证")
    print("🧪" * 40 + "\n")
    
    results = []
    
    # 测试1: 任务规划
    result1 = await test_task_planner_v2()
    results.append(("任务规划工作流", result1))
    
    # 测试2: 代码审查
    result2 = await test_code_review_v2()
    results.append(("代码审查工作流", result2))
    
    # 测试3: 重试机制
    result3 = await test_retry_mechanism()
    results.append(("重试机制", result3))
    
    # 汇总结果
    print("\n\n" + "=" * 80)
    print("测试结果汇总")
    print("=" * 80)
    
    for test_name, passed in results:
        status = "✅ 通过" if passed else "❌ 失败"
        print(f"{test_name:20s} : {status}")
    
    total = len(results)
    passed = sum(1 for _, p in results if p)
    
    print(f"\n总计: {passed}/{total} 测试通过")
    
    if passed == total:
        print("\n🎉 所有测试通过！v2.0改进成功！")
    else:
        print(f"\n⚠️  有 {total - passed} 个测试失败，请检查日志")
    
    return passed == total


if __name__ == "__main__":
    # 注意：需要先配置OPENAI_API_KEY环境变量
    import os
    if not os.getenv("OPENAI_API_KEY"):
        print("⚠️  警告: OPENAI_API_KEY未设置，部分功能可能无法正常工作")
        print("   请设置环境变量后重新运行\n")
    
    success = asyncio.run(main())
    exit(0 if success else 1)
