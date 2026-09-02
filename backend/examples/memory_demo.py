"""记忆功能使用示例"""

import asyncio
from uuid import uuid4
from app.memory import MemoryManager


async def demo_basic_memory():
    """演示基本的记忆功能"""
    print("=" * 60)
    print("演示1: 基本记忆功能")
    print("=" * 60)
    
    # 创建记忆管理器
    session_id = str(uuid4())
    memory = MemoryManager(session_id=session_id)
    await memory.initialize()
    
    # 添加几条消息
    print("\n添加用户消息...")
    await memory.add_message("user", "你好，我想学习Python编程")
    
    print("添加助手回复...")
    await memory.add_message("assistant", "很好！Python是一门非常适合初学者的语言。你想从哪个方面开始？")
    
    print("\n添加第二轮对话...")
    await memory.add_message("user", "我想先了解变量和数据类型")
    await memory.add_message("assistant", "好的！Python有多种数据类型，包括整数、浮点数、字符串、列表等。")
    
    # 获取上下文
    print("\n" + "=" * 60)
    print("当前记忆上下文:")
    print("=" * 60)
    context = await memory.get_context()
    print(context)
    
    # 获取统计信息
    print("\n" + "=" * 60)
    print("记忆统计:")
    print("=" * 60)
    stats = await memory.get_stats()
    print(f"会话ID: {stats['session_id']}")
    print(f"短期记忆消息数: {stats['short_term_message_count']}")
    print(f"是否有长期摘要: {stats['long_term_has_summary']}")
    
    print("\n✓ 基本记忆功能演示完成\n")


async def demo_memory_with_persistence(db_session=None):
    """演示带持久化的记忆功能"""
    print("=" * 60)
    print("演示2: 带数据库持久化的记忆")
    print("=" * 60)
    
    session_id = str(uuid4())
    
    try:
        # 创建带数据库会话的记忆管理器
        memory = MemoryManager(
            session_id=session_id,
            db_session=db_session
        )
        await memory.initialize()
        
        # 添加消息
        messages = [
            ("user", "帮我写一个计算斐波那契数列的函数"),
            ("assistant", "好的，这是一个递归实现的斐波那契函数：\n\ndef fibonacci(n):\n    if n <= 1:\n        return n\n    return fibonacci(n-1) + fibonacci(n-2)"),
            ("user", "这个实现效率怎么样？"),
            ("assistant", "递归实现简单但效率较低，时间复杂度是O(2^n)。建议使用动态规划优化。"),
            ("user", "请给我优化版本"),
        ]
        
        for role, content in messages:
            await memory.add_message(role, content)
            print(f"已添加 {role} 消息")
        
        # 保存并重新加载
        await memory.save_to_db()
        print("\n记忆已保存到数据库")
        
        # 获取上下文
        context = await memory.get_context()
        print("\n" + "=" * 60)
        print("从数据库加载的上下文:")
        print("=" * 60)
        print(context[:500] + "..." if len(context) > 500 else context)
        
        # 获取统计
        stats = await memory.get_stats()
        print("\n" + "=" * 60)
        print("数据库统计:")
        print("=" * 60)
        if 'database' in stats:
            print(f"总消息数: {stats['database'].get('total_messages', 0)}")
            print(f"用户消息: {stats['database'].get('user_messages', 0)}")
            print(f"助手消息: {stats['database'].get('assistant_messages', 0)}")
        
        print("\n✓ 持久化记忆演示完成\n")
        
    except Exception as e:
        print(f"\n⚠ 持久化演示跳过（需要数据库连接）: {str(e)}\n")


async def demo_memory_window():
    """演示短期记忆窗口机制"""
    print("=" * 60)
    print("演示3: 短期记忆窗口（只保留最近N轮）")
    print("=" * 60)
    
    session_id = str(uuid4())
    # 设置窗口大小为3轮
    memory = MemoryManager(session_id=session_id)
    await memory.initialize()
    
    # 添加超过窗口的消息
    print("\n添加8条消息（窗口大小为3，应该只保留最近6条）...")
    for i in range(8):
        await memory.add_message("user", f"这是第{i+1}条用户消息")
        await memory.add_message("assistant", f"这是第{i+1}条助手回复")
    
    # 获取短期记忆
    short_term = await memory.get_short_term_messages()
    print(f"\n短期记忆中的消息数: {len(short_term)}")
    print("\n最近的3轮对话:")
    for i, msg in enumerate(short_term[-6:], 1):  # 最后6条 = 3轮
        print(f"{i}. {msg['role']}: {msg['content'][:30]}...")
    
    print("\n✓ 短期记忆窗口演示完成\n")


async def demo_agent_with_memory():
    """演示Agent使用记忆"""
    print("=" * 60)
    print("演示4: Agent集成记忆")
    print("=" * 60)
    
    from app.agents.coder import CoderAgent
    
    # 创建Agent
    agent = CoderAgent(agent_id=uuid4(), name="DemoCoder")
    await agent.initialize()
    
    # 为Agent设置记忆
    session_id = str(uuid4())
    await agent.set_memory(session_id=session_id)
    
    print(f"\nAgent已设置记忆，会话ID: {session_id}")
    
    # 第一次对话
    print("\n第一轮对话...")
    result1 = await agent.execute_with_memory({
        "user_input": "帮我写一个简单的Python函数，计算两个数的和",
        "requirement": "加法函数",
        "language": "python"
    })
    
    if result1.get("success"):
        print(f"✓ 第一轮成功")
        print(f"输出预览: {result1.get('output', '')[:100]}...")
    
    # 第二次对话（应该能记住之前的内容）
    print("\n第二轮对话（基于之前的上下文）...")
    result2 = await agent.execute_with_memory({
        "user_input": "现在帮我写一个减法函数",
        "requirement": "减法函数",
        "language": "python"
    })
    
    if result2.get("success"):
        print(f"✓ 第二轮成功")
        print(f"输出预览: {result2.get('output', '')[:100]}...")
    
    # 检查记忆状态
    if agent.memory_manager:
        stats = await agent.memory_manager.get_stats()
        print(f"\n记忆状态:")
        print(f"  - 短期记忆消息数: {stats['short_term_message_count']}")
        print(f"  - 有长期摘要: {stats['long_term_has_summary']}")
    
    print("\n✓ Agent记忆集成演示完成\n")


async def main():
    """运行所有演示"""
    print("\n" + "=" * 60)
    print("多Agent平台 - 长短期记忆功能演示")
    print("=" * 60 + "\n")
    
    try:
        # 演示1: 基本记忆
        await demo_basic_memory()
        
        # 演示2: 带持久化（可选，需要数据库）
        # await demo_memory_with_persistence()
        
        # 演示3: 短期记忆窗口
        await demo_memory_window()
        
        # 演示4: Agent集成
        await demo_agent_with_memory()
        
        print("=" * 60)
        print("所有演示完成！🎉")
        print("=" * 60)
        
    except Exception as e:
        print(f"\n❌ 演示过程中发生错误: {str(e)}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    # 设置环境变量以使用SQLite
    import os
    os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///./multi_agent.db"
    
    asyncio.run(main())
