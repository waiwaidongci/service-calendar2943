"""十万单级规模与分批正确性验证（仅标准库，python -I 运行）。

校验：
1. 分批（BATCH_SIZE）结果与 batch_size=极大（单批全局）完全一致；
2. 存在跨批界配对（结转机制确实在批界两侧生效）；
3. 结转区规模有界（远小于全量，不铺开所有候选对）；
4. 每样本至多使用一次，时间窗与同优先级约束不被破坏；
5. 大样本量下耗时与检查点体积可接受；
6. 从中断点恢复与一次性运行结果一致。
"""
import random
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.services.pairing import run_match, BATCH_SIZE, WINDOW_SECONDS  # noqa: E402


def make_entries(rng, n, tag):
    prio = ['普通', '紧急', '低']
    rows = []
    base = 0
    for i in range(n):
        # 密集时间戳：分钟级抖动，制造大量跨批界候选。
        minute = base + rng.randint(0, 6)
        base = minute
        day = 1 + minute // (12 * 60)
        hh = 9 + (minute % (12 * 60)) // 60
        mm = minute % 60
        rows.append({
            'period': '2026-11',
            'department': tag,
            'opened': f'2026-11-{min(day,28):02d}T{hh:02d}:{mm:02d}:00+08:00',
            'closed': f'2026-11-{min(day,28):02d}T{hh+1:02d}:00:00+08:00',
            'priority': prio[rng.randrange(3)],
        })
    return rows


def assert_constraints(entries_a, entries_b, pairs):
    used_a, used_b = set(), set()
    from datetime import datetime
    map_a = {ln: r for ln, r in enumerate(entries_a, 1)}
    map_b = {ln: r for ln, r in enumerate(entries_b, 1)}
    for la, lb, gap in pairs:
        assert la not in used_a and lb not in used_b, '样本被重复使用'
        used_a.add(la)
        used_b.add(lb)
        ra, rb = map_a[la], map_b[lb]
        assert ra['priority'] == rb['priority'], '跨优先级配对'
        ta = datetime.fromisoformat(ra['opened'])
        tb = datetime.fromisoformat(rb['opened'])
        assert abs((ta - tb).total_seconds()) <= WINDOW_SECONDS, '超出时间窗'
        assert int(abs((ta - tb).total_seconds())) == int(gap), '时间差记录错误'


def main():
    n = 100_000
    rng = random.Random(7)
    a = make_entries(rng, n, 'A组')
    b = make_entries(rng, n, 'B组')

    t0 = time.perf_counter()
    max_carry = 0
    batches = 0

    def watch(cp):
        nonlocal max_carry, batches
        max_carry = max(max_carry, len(cp['carry_a']), len(cp['carry_b']))
        batches += 1

    pairs, stats, final = run_match(a, b, on_batch=watch)
    elapsed = time.perf_counter() - t0

    # 单批全局基线
    base_pairs, base_stats, _ = run_match(a, b, batch_size=10 ** 9)
    assert pairs == base_pairs, '分批与全局配对不一致'
    assert stats == base_stats, '分批与全局统计不一致'

    # 约束与计数
    assert_constraints(a, b, pairs)
    assert stats['pair_count'] == len(pairs)
    assert len({p[0] for p in pairs}) == len(pairs)
    assert len({p[1] for p in pairs}) == len(pairs)

    # 跨批界配对存在性：找一对行号跨越 BATCH_SIZE 边界的配对
    crossed = any((la - 1) // BATCH_SIZE != (lb - 1) // BATCH_SIZE
                  for la, lb, _ in pairs)

    # 断点恢复：取第一批后的检查点续跑
    captured = {}
    run_match(a, b, on_batch=lambda cp: captured.setdefault('cp', cp))
    resume_pairs, resume_stats, _ = run_match(a, b, state=captured['cp'])
    assert resume_pairs == pairs and resume_stats == stats, '中断续跑结果不一致'

    import json
    cp_size = len(json.dumps(captured['cp'], ensure_ascii=False))

    print(f'样本量: 每组 {n:,}，共 {2*n:,}')
    print(f'批次数: {batches}（批大小 {BATCH_SIZE:,}），最大结转区: {max_carry} 条')
    print(f'配对数: {stats["pair_count"]:,}，时间差合计: {stats["gap_seconds_total"]:,} 秒')
    print(f'未配对: A={stats["group_a_unpaired"]:,} B={stats["group_b_unpaired"]:,}')
    print(f'跨批界配对存在: {crossed}')
    print(f'耗时: {elapsed:.2f}s；首个检查点 JSON 体积: {cp_size/1024:.1f} KB')
    assert elapsed < 30, '十万单配对耗时超阈值'
    assert max_carry < BATCH_SIZE, '结转区应小于单批规模'
    assert crossed, '未观察到跨批界配对，结转机制可能失效'
    print('规模、分批、结转、约束、续跑全部校验通过')


if __name__ == '__main__':
    main()
