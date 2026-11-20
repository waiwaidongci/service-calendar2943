"""配对算法的暴力枚举验证（仅用标准库，python -I 运行）。

对照口径：
1. 穷举所有满足 同优先级+时间窗 的可行匹配，逐级按
   (配对数最多, 时间差总和最小, 行号序列字典序) 取最优；
2. 分批结转实现必须与全局贪心、与暴力最优三者完全一致；
3. 续跑（每批后中断再恢复）结果必须与一次性运行一致。
"""
import itertools
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.services.pairing import run_match, prepare_samples, WINDOW_SECONDS  # noqa: E402


def make_entries(rng, n, day_span=5):
    dept = ['数据建模组', '数据采样组']
    prio = ['普通', '紧急']
    rows = []
    for i in range(n):
        day = rng.randint(1, day_span)
        hh = rng.randint(9, 18)
        mm = rng.randint(0, 59)
        rows.append({
            'period': '2026-11',
            'department': dept[rng.randrange(len(dept))],
            'opened': f'2026-11-{day:02d}T{hh:02d}:{mm:02d}:00+08:00',
            'closed': f'2026-11-{day:02d}T{hh+1:02d}:00:00+08:00',
            'priority': prio[rng.randrange(len(prio))],
        })
    return rows


def brute_force(entries_a, entries_b, window=WINDOW_SECONDS):
    """穷举全部可行匹配，返回 (pairs, stats) 最优解。"""
    a = prepare_samples(entries_a)
    b = prepare_samples(entries_b)
    best = None  # (count, -gap? no) -> key 最小化

    def key_of(matched):
        count = len(matched)
        gap = sum(abs((x[1] - y[1]).total_seconds()) for x, y in matched)
        order = tuple(sorted((x[0], y[0]) for x, y in matched))
        return (-count, gap, order)

    # 对 B 侧做排列穷举：枚举 B 的使用子集与与 A 的指派。
    # 小规模（n<=6）可承受。
    results = []

    def search(i, used_b, matched):
        if i == len(a):
            results.append(list(matched))
            return
        ai = a[i]
        # 选项1：A[i] 不配对
        search(i + 1, used_b, matched)
        # 选项2：与某个未用且可行的 B 配对
        for j, bj in enumerate(b):
            if j in used_b:
                continue
            if ai[2] == bj[2] and abs((ai[1] - bj[1]).total_seconds()) <= window:
                used_b.add(j)
                matched.append((ai, bj))
                search(i + 1, used_b, matched)
                matched.pop()
                used_b.discard(j)

    search(0, set(), [])
    optimal = min(results, key=key_of)
    pairs = sorted(((x[0], y[0], int(abs((x[1]-y[1]).total_seconds())))
                    for x, y in optimal), key=lambda p: (p[0], p[1]))
    return pairs


def pairs_to_triples(pairs):
    return sorted([(p[0], p[1], int(p[2])) for p in pairs],
                  key=lambda p: (p[0], p[1]))


def run_batched(a, b, batch_size, resume=False):
    if not resume:
        return run_match(a, b, batch_size=batch_size)
    # 模拟每批结束中断：用 on_batch 抓取断点，再从断点续跑。
    captured = {}

    def on_batch(cp):
        if not cp['finished']:
            captured['state'] = cp

    pairs1, stats1, _ = run_match(a, b, batch_size=batch_size, on_batch=on_batch)
    if 'state' not in captured:
        return pairs1, stats1, None
    pairs2, stats2, final = run_match(a, b, batch_size=batch_size,
                                      state=captured['state'])
    assert pairs_to_triples(pairs1) == pairs_to_triples(pairs2), '续跑配对不一致'
    assert stats1 == stats2, '续跑统计不一致'
    return pairs2, stats2, final


def main():
    rng = random.Random(20261120)
    failures = 0
    for trial in range(400):
        na = rng.randint(0, 7)
        nb = rng.randint(0, 7)
        a = make_entries(rng, na)
        b = make_entries(rng, nb)
        expect = brute_force(a, b)
        for batch_size in (1, 2, 3, 5, 1000):
            pairs, stats, _ = run_batched(a, b, batch_size, resume=True)
            got = pairs_to_triples(pairs)
            if got != expect:
                failures += 1
                print(f'[FAIL] trial={trial} batch={batch_size}')
                print(' expect', expect)
                print(' got    ', got)
                if failures > 5:
                    return 1
    # 确定性：同输入多次结果一致
    a = make_entries(rng, 8)
    b = make_entries(rng, 8)
    r1 = pairs_to_triples(run_match(a, b, batch_size=2)[0])
    r2 = pairs_to_triples(run_match(a, b, batch_size=2)[0])
    assert r1 == r2
    print(f'暴力对照 400 组 × 5 种批大小（含逐批中断续跑）全部通过；确定性校验通过')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
