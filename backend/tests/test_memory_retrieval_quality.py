"""检索质量回归测试：中文 bigram 错位召回 / 停用词稀释 / entity 匹配

复用 test_memory_phase2 的 FakeSession 基建，不依赖真实数据库。
通过 `python tests/test_memory_retrieval_quality.py` 直接运行。
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("OPENAI_API_KEY", "")

from app.memory.retriever import MemoryRetriever, _relevance, _tokenize  # noqa: E402
from test_memory_phase2 import FakeSession, make_entry, run  # noqa: E402

PASSED = []
FAILED = []


def ok(name, condition):
    if condition:
        PASSED.append(name)
    else:
        FAILED.append(name)
    print(f"  [{'PASS' if condition else 'FAIL'}] {name}")


# ---------- 中文滑动窗口 bigram ----------

def test_bigram_shifted_alignment():
    # 内容「用户喜欢喝咖啡」原分词只产生「喝咖」，查询「咖啡」时相关度为 0
    tokens = _tokenize("咖啡")
    ok("错位 bigram：查询「咖啡」可命中「喝咖啡」",
       _relevance("用户喜欢喝咖啡", tokens) > 0)
    tokens2 = _tokenize("喝咖啡")
    ok("反向错位：查询「喝咖啡」可命中「咖啡」",
       _relevance("咖啡", tokens2) > 0)


def test_bigram_short_content():
    # 两字内容与三字查询互相命中
    tokens = _tokenize("咖啡")
    ok("两字内容被滑窗查询命中", _relevance("咖啡", tokens) > 0)


# ---------- 停用词 ----------

def test_stopwords_dilution():
    q = _tokenize("could you please tell me what kind of coffee the user likes")
    ok("英文停用词被过滤（token 数下降）", len(q) < 10)
    rel = _relevance("coffee", q)
    ok("过滤后关键词命中率提升", rel >= 0.2)


def test_chinese_stopwords_removed():
    q = _tokenize("我想要一杯咖啡")
    ok("中文停用词片段被过滤", "想要" not in q)


# ---------- entity 参与匹配 ----------

def test_entity_query_ranks():
    entity_hit = make_entry("喜欢钓鱼", memory_type="fact", strength=0.5, entity="Peter")
    other = make_entry("今天是周二", memory_type="fact", strength=0.9, entity="weather")
    session = FakeSession(rows=[entity_hit, other])
    results = run(MemoryRetriever(top_k=5).retrieve(session, None, query="peter"))
    ok("按实体提问时 entity 命中的记忆排第一",
       len(results) == 2 and results[0]["content"] == "喜欢钓鱼")


def test_entity_query_no_false_positive():
    # 无关实体不应因 entity 匹配被顶到前面
    a = make_entry("喜欢钓鱼", memory_type="fact", strength=0.5, entity="Peter")
    b = make_entry("peter 昨天来公司", memory_type="fact", strength=0.9, entity="office")
    session = FakeSession(rows=[a, b])
    results = run(MemoryRetriever(top_k=5).retrieve(session, None, query="peter"))
    ok("内容命中的强记忆仍排第一",
       len(results) == 2 and results[0]["content"] == "peter 昨天来公司")


if __name__ == "__main__":
    test_bigram_shifted_alignment()
    test_bigram_short_content()
    test_stopwords_dilution()
    test_chinese_stopwords_removed()
    test_entity_query_ranks()
    test_entity_query_no_false_positive()
    print(f"\n{len(PASSED)} passed, {len(FAILED)} failed")
    for f in FAILED:
        print(f"  FAILED: {f}")
    sys.exit(1 if FAILED else 0)
