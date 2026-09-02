"""
Agent使用示例
展示如何创建和使用自定义Agent
"""

import asyncio
from uuid import uuid4
from app.agents.coder import CoderAgent
from app.agents.reviewer import ReviewerAgent
from app.agents.registry import agent_registry


async def example_single_agent():
    """单个Agent使用示例"""
    print("=" * 50)
    print("Example 1: Single Agent Execution")
    print("=" * 50)
    
    # 创建Coder Agent
    coder = CoderAgent(
        agent_id=uuid4(),
        name="Python Coder"
    )
    
    # 初始化
    await coder.initialize()
    
    # 执行任务
    result = await coder.execute({
        "requirement": "创建一个Python函数，计算斐波那契数列",
        "language": "python",
        "context": "需要高效实现，支持大数计算"
    })
    
    print(f"\nSuccess: {result['success']}")
    print(f"Code:\n{result.get('code', 'N/A')}")
    print(f"\nExplanation: {result.get('explanation', 'N/A')}")


async def example_agent_registry():
    """Agent注册中心使用示例"""
    print("\n" + "=" * 50)
    print("Example 2: Agent Registry")
    print("=" * 50)
    
    # 创建多个Agent
    coder = CoderAgent(agent_id=uuid4(), name="Coder")
    reviewer = ReviewerAgent(agent_id=uuid4(), name="Reviewer")
    
    # 注册Agent
    agent_registry.register(coder)
    agent_registry.register(reviewer)
    
    # 列出所有Agent
    agents = agent_registry.list_agents()
    print(f"\nRegistered Agents: {len(agents)}")
    for agent in agents:
        print(f"  - {agent['name']}: {agent['capabilities']}")
    
    # 根据能力查找Agent
    coding_agents = agent_registry.get_agents_by_capability("code_generation")
    print(f"\nAgents with 'code_generation' capability: {len(coding_agents)}")


async def example_multi_agent_collaboration():
    """多Agent协作示例"""
    print("\n" + "=" * 50)
    print("Example 3: Multi-Agent Collaboration")
    print("=" * 50)
    
    # 创建Agent
    coder = CoderAgent(agent_id=uuid4(), name="Coder")
    reviewer = ReviewerAgent(agent_id=uuid4(), name="Reviewer")
    
    await coder.initialize()
    await reviewer.initialize()
    
    # 第一步：Coder生成代码
    print("\n[Step 1] Coder generating code...")
    code_result = await coder.execute({
        "requirement": "实现一个简单的HTTP客户端",
        "language": "python"
    })
    
    generated_code = code_result.get('code', '')
    print(f"Generated code length: {len(generated_code)} chars")
    
    # 第二步：Reviewer审查代码
    print("\n[Step 2] Reviewer analyzing code...")
    review_result = await reviewer.execute({
        "code": generated_code,
        "language": "python",
        "focus_areas": ["quality", "security"]
    })
    
    print(f"\nReview completed:")
    print(review_result.get('review', 'N/A'))


async def main():
    """运行所有示例"""
    try:
        await example_single_agent()
        await example_agent_registry()
        await example_multi_agent_collaboration()
        
        print("\n" + "=" * 50)
        print("All examples completed successfully!")
        print("=" * 50)
    except Exception as e:
        print(f"\nError: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    # 注意：需要先配置OPENAI_API_KEY环境变量
    asyncio.run(main())
