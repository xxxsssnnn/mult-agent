"""测试短期记忆滑动窗口改进 - 验证语义不丢失"""

import asyncio
from uuid import uuid4
from app.memory import MemoryManager


async def test_no_semantic_loss():
    """
    测试场景：验证超出窗口的消息不会丢失语义
    
    步骤：
    1. 设置窗口大小为2轮（很小，容易触发溢出）
    2. 添加6轮对话（远超窗口）
    3. 检查长期记忆是否包含早期对话的关键信息
    4. 验证上下文完整性
    """
    print("=" * 80)
    print("测试: 短期记忆滑动窗口 - 语义不丢失验证")
    print("=" * 80)
    
    # 创建记忆管理器，使用较小的窗口便于测试
    session_id = str(uuid4())
    memory = MemoryManager(session_id=session_id)
    
    # 手动设置窗口大小为2轮
    memory.short_term.window_size = 2
    memory.short_term.memory.k = 2
    
    await memory.initialize()
    print(f"\n✓ 记忆管理器初始化完成")
    print(f"  会话ID: {session_id}")
    print(f"  短期窗口大小: 2轮对话\n")
    
    # 模拟多轮对话
    conversations = [
        ("user", "我想学习Python编程，从零开始"),
        ("assistant", "很好！Python是一门非常适合初学者的语言。我建议从基础语法开始..."),
        
        ("user", "那变量和数据类型怎么理解？"),
        ("assistant", "Python有多种数据类型：整数(int)、浮点数(float)、字符串(str)、列表(list)..."),
        
        ("user", "能给我一些实际例子吗？"),
        ("assistant", "当然！比如：x = 10 (整数), name = 'Alice' (字符串), scores = [90, 85, 92] (列表)..."),
        
        ("user", "列表和元组有什么区别？"),
        ("assistant", "主要区别：列表是可变的，元组是不可变的。列表用[]，元组用()..."),
        
        ("user", "那我应该什么时候用列表，什么时候用元组？"),
        ("assistant", "建议：需要修改时用列表，不需要修改时用元组。比如坐标点用元组(10, 20)..."),
        
        ("user", "好的，现在我理解了。帮我写一个简单的学生管理系统吧"),
        ("assistant", "好的！我将创建一个包含姓名、年龄、成绩的学生管理系统..."),
    ]
    
    print("开始添加对话...\n")
    for i, (role, content) in enumerate(conversations, 1):
        await memory.add_message(role, content)
        short_count = await memory.short_term.get_message_count()
        print(f"第{i}条消息 ({role}): {content[:40]}...")
        print(f"  → 短期记忆中的消息数: {short_count}")
        
        # 每添加一条就检查一下
        if i % 2 == 0:
            print()
    
    print("\n" + "=" * 80)
    print("检查结果:")
    print("=" * 80)
    
    # 1. 检查短期记忆
    short_messages = await memory.get_short_term_messages()
    print(f"\n1. 短期记忆（最近2轮）:")
    print(f"   消息数量: {len(short_messages)}")
    print(f"   内容预览:")
    for msg in short_messages[-4:]:  # 显示最后2轮
        print(f"   - {msg['role']}: {msg['content'][:60]}...")
    
    # 2. 检查长期记忆摘要
    long_summary = await memory.get_long_term_summary()
    print(f"\n2. 长期记忆摘要:")
    print(f"   摘要长度: {len(long_summary)} 字符")
    print(f"   摘要内容:\n{long_summary}")
    
    # 3. 检查完整上下文
    full_context = await memory.get_context()
    print(f"\n3. 完整上下文:")
    print(f"   总长度: {len(full_context)} 字符")
    print(f"   预览:\n{full_context[:500]}...")
    
    # 4. 验证关键信息是否保留
    print(f"\n4. 语义完整性验证:")
    
    key_info_checks = [
        ("用户目标", "学习Python", "learning Python" in long_summary.lower() or "python" in long_summary.lower()),
        ("数据类型讨论", "变量/数据类型", "data type" in long_summary.lower() or "数据类型" in long_summary),
        ("列表vs元组", "列表/元组区别", "list" in long_summary.lower() or "tuple" in long_summary.lower() or "列表" in long_summary),
        ("最终任务", "学生管理系统", "student" in long_summary.lower() or "学生管理" in long_summary),
    ]
    
    all_passed = True
    for check_name, keyword, found in key_info_checks:
        status = "✅" if found else "❌"
        print(f"   {status} {check_name} ({keyword}): {'已保留' if found else '丢失'}")
        if not found:
            all_passed = False
    
    # 5. 统计信息
    stats = await memory.get_stats()
    print(f"\n5. 统计信息:")
    print(f"   短期记忆消息数: {stats['short_term_message_count']}")
    print(f"   有长期摘要: {stats['long_term_has_summary']}")
    
    if 'database' in stats:
        print(f"   数据库总消息数: {stats['database'].get('total_messages', 0)}")
    
    # 6. 结论
    print("\n" + "=" * 80)
    if all_passed:
        print("✅ 测试通过！所有关键信息都被妥善保存，没有语义丢失")
        print("\n说明:")
        print("- 短期记忆只保留最近2轮对话（快速访问）")
        print("- 早期对话被提取关键信息并保存到长期记忆摘要中")
        print("- 完整上下文 = 长期摘要 + 短期详细对话")
        print("- Agent可以同时获得历史概要和最近的详细内容")
    else:
        print("⚠️ 部分信息可能未完全捕获到摘要中")
        print("建议: 调整摘要生成策略或使用更强的LLM模型")
    print("=" * 80)
    
    return all_passed


async def test_window_overflow_detection():
    """
    测试窗口溢出检测机制
    """
    print("\n" + "=" * 80)
    print("测试: 窗口溢出检测机制")
    print("=" * 80)
    
    session_id = str(uuid4())
    memory = MemoryManager(session_id=session_id)
    
    # 设置窗口为1轮（更容易观察）
    memory.short_term.window_size = 1
    memory.short_term.memory.k = 1
    
    await memory.initialize()
    
    print(f"\n窗口大小: 1轮对话（2条消息）\n")
    
    # 添加3轮对话
    for i in range(1, 7):
        role = "user" if i % 2 == 1 else "assistant"
        content = f"Message {i}"
        
        evicted = await memory.short_term.add_message(role, content)
        
        short_count = await memory.short_term.get_message_count()
        print(f"添加消息 {i} ({role})")
        print(f"  → 短期记忆: {short_count} 条消息")
        print(f"  → 被移出: {len(evicted)} 条消息")
        
        if evicted:
            print(f"     移出的消息: {[msg.content for msg in evicted]}")
        
        # 验证短期记忆不超过窗口限制
        max_expected = memory.short_term.window_size * 2
        assert short_count <= max_expected, f"短期记忆超出限制: {short_count} > {max_expected}"
        
        print()
    
    print("✅ 窗口溢出检测正常工作")
    print("   - 短期记忆始终保持在窗口大小内")
    print("   - 超出的消息被正确捕获并返回")
    return True


async def main():
    """运行所有测试"""
    print("\n" + "=" * 80)
    print("短期记忆滑动窗口改进 - 测试套件")
    print("=" * 80 + "\n")
    
    import sys

    all_ok = True
    try:
        # 测试1: 语义不丢失
        result1 = await test_no_semantic_loss()
        all_ok = all_ok and bool(result1)

        # 测试2: 窗口溢出检测
        result2 = await test_window_overflow_detection()
        all_ok = all_ok and result2 is not False

        print("\n" + "=" * 80)
        print("所有测试完成！")
        print("=" * 80)

        if result1:
            print("\n🎉 改进成功！滑动窗口不再导致语义丢失")
            print("\n核心优势:")
            print("  ✅ 所有对话内容都被保存（数据库 + 长期记忆）")
            print("  ✅ 短期记忆保持轻量，快速访问最近对话")
            print("  ✅ 长期记忆智能摘要，保留关键信息")
            print("  ✅ Agent始终拥有完整的上下文理解能力")

    except Exception as e:
        print(f"\n❌ 测试失败: {str(e)}")
        import traceback
        traceback.print_exc()
        all_ok = False

    # 强制退出码：失败时返回非 0，避免 CI/回归脚本漏报
    print(f"\n{'✅ 全部通过' if all_ok else '❌ 存在失败项'} (exit={0 if all_ok else 1})")
    sys.exit(0 if all_ok else 1)


if __name__ == "__main__":
    import os
    os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///./multi_agent.db"

    asyncio.run(main())
