"""
工作流使用示例
展示如何使用LangGraph工作流引擎
"""

import asyncio
from uuid import uuid4
from app.agents.coder import CoderAgent
from app.agents.reviewer import ReviewerAgent
from app.workflows.code_review import CodeReviewWorkflow
from app.workflows.task_planner import TaskPlannerWorkflow


async def example_code_review_workflow():
    """代码审查工作流示例"""
    print("=" * 60)
    print("Example 1: Code Review Workflow")
    print("=" * 60)
    
    # 创建Agent
    coder = CoderAgent(agent_id=uuid4(), name="Coder")
    reviewer = ReviewerAgent(agent_id=uuid4(), name="Reviewer")
    
    await coder.initialize()
    await reviewer.initialize()
    
    # 创建工作流
    workflow = CodeReviewWorkflow(
        coder_agent=coder,
        reviewer_agent=reviewer,
        max_iterations=2
    )
    
    # 执行工作流
    print("\n[Workflow] Starting code generation and review...")
    result = await workflow.execute({
        "requirement": "创建一个Python装饰器，用于函数执行时间统计",
        "language": "python"
    })
    
    print(f"\nSuccess: {result['success']}")
    if result['success']:
        print(f"\nGenerated Code:\n{result.get('code', 'N/A')[:500]}...")
        print(f"\nReview Result:\n{result.get('review', 'N/A')[:300]}...")
        print(f"\nApproved: {result.get('approved')}")
        print(f"Iterations: {result.get('iterations')}")


async def example_task_planner_workflow():
    """任务规划工作流示例"""
    print("\n" + "=" * 60)
    print("Example 2: Task Planner Workflow")
    print("=" * 60)
    
    # 创建工作流
    workflow = TaskPlannerWorkflow()
    
    # 执行工作流
    print("\n[Workflow] Starting task planning...")
    result = await workflow.execute({
        "user_input": "构建一个完整的用户认证系统，包括注册、登录、Token管理"
    })
    
    print(f"\nSuccess: {result['success']}")
    if result['success']:
        print(f"\nStatus: {result['status']}")
        print(f"Total Tasks: {len(result.get('tasks', []))}")
        print(f"\nTasks:")
        for i, task in enumerate(result.get('tasks', []), 1):
            print(f"  {i}. {task['title']} - {task['status']}")
        
        print(f"\nResults:")
        for i, res in enumerate(result.get('results', []), 1):
            print(f"  {i}. Task {res['task_id']}: {res['output']}")


async def main():
    """运行所有示例"""
    try:
        await example_code_review_workflow()
        await example_task_planner_workflow()
        
        print("\n" + "=" * 60)
        print("All workflow examples completed successfully!")
        print("=" * 60)
    except Exception as e:
        print(f"\nError: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    # 注意：需要先配置OPENAI_API_KEY环境变量
    asyncio.run(main())
