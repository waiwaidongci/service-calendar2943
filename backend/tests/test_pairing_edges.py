"""配对算法边界场景验证（python -I，仅标准库）。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.services.pairing import run_match  # noqa: E402


def src(opened, priority, dept='X'):
    return {'period': '2026-11', 'department': dept, 'opened': opened,
            'closed': opened, 'priority': priority}


def main():
    # 1. 完全相同底稿：每条都应配上，时间差总和为 0
    base = [src(f'2026-11-{1+i:02d}T10:00:00+08:00', '普通') for i in range(10)]
    pairs, stats, _ = run_match(base, [dict(x) for x in base], batch_size=3)
    assert stats['pair_count'] == 10
    assert stats['gap_seconds_total'] == 0
    assert pairs == sorted(pairs, key=lambda p: (p[0], p[1]))
    assert all(p[0] == p[1] for p in pairs)

    # 2. 同刻但优先级全不同：零配对
    a = [src('2026-11-01T10:00:00+08:00', '普通')]
    b = [src('2026-11-01T10:00:00+08:00', '紧急')]
    pairs, stats, _ = run_match(a, b)
    assert stats['pair_count'] == 0
    assert stats['group_a_unpaired'] == 1 and stats['group_b_unpaired'] == 1

    # 3. 时间窗边界：恰好30分钟可配，31分钟不可配
    a = [src('2026-11-01T10:00:00+08:00', '普通'),
         src('2026-11-01T11:00:00+08:00', '普通')]
    b = [src('2026-11-01T10:30:00+08:00', '普通'),
         src('2026-11-01T11:31:00+08:00', '普通')]
    pairs, stats, _ = run_match(a, b)
    assert stats['pair_count'] == 1
    assert pairs[0][2] == 1800  # 恰好 30 分钟

    # 4. 一侧为空
    pairs, stats, cp = run_match(base, [], batch_size=3)
    assert stats['pair_count'] == 0
    assert stats['group_b_total'] == 0 and stats['group_a_unpaired'] == 10
    assert cp['finished'] is True

    # 5. 多优先级混合 + 批界：每单用一次且不串优先级
    a, b = [], []
    for i in range(20):
        a.append(src(f'2026-11-{1+i//4:02d}T{9+i%8:02d}:00:00+08:00',
                     '普通' if i % 2 else '紧急'))
        b.append(src(f'2026-11-{1+i//4:02d}T{9+i%8:02d}:05:00+08:00',
                     '普通' if i % 2 else '紧急'))
    pairs, stats, _ = run_match(a, b, batch_size=4)
    for la, lb, gap in pairs:
        assert a[la-1]['priority'] == b[lb-1]['priority']
        assert gap == 300
    assert len({p[0] for p in pairs}) == len(pairs)
    assert len({p[1] for p in pairs}) == len(pairs)

    # 6. 时间差总和最小化的构造性验证：
    # A1@00、A2@10；B1@05、B2@12，同优先级。最大配对数为2，
    # 最优指派 (A1,B1)+(A2,B2) 时间差=5+2=7；交叉指派=12+5=17。
    a = [src('2026-11-01T10:00:00+08:00', '普通'),
         src('2026-11-01T10:10:00+08:00', '普通')]
    b = [src('2026-11-01T10:05:00+08:00', '普通'),
         src('2026-11-01T10:12:00+08:00', '普通')]
    pairs, stats, _ = run_match(a, b)
    assert stats['pair_count'] == 2
    assert stats['gap_seconds_total'] == 7 * 60
    assert [(p[0], p[1]) for p in pairs] == [(1, 1), (2, 2)]

    print('边界场景 6 组全部通过：全同底稿、同刻异优先级、30分钟边界、')
    print('空组、多优先级批界、最小时间差指派')


if __name__ == '__main__':
    main()
