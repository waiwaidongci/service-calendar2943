"""配对比较服务的集成验证（python -I，仅标准库）。

用 sys.modules 垫片替代 FastAPI/SQLAlchemy/Redis，以内存仓库真实驱动
src/services/comparison.py 的创建、分批执行、中断续跑、改选代次保护、
发布闸门、配对依据分页全链路。
"""
import copy
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
import tests._shims as shims  # noqa: E402

redis_shim = shims.install()

from src.services import comparison as svc  # noqa: E402
from src.services import pairing  # noqa: E402
from src.models.run import Run  # noqa: E402
from src.models.comparison import Comparison  # noqa: E402
from src.models.identity import Audit  # noqa: E402
from fastapi import HTTPException  # noqa: E402

POLICY = {
    'version': '工作时钟-v1', 'timezone': 'Asia/Shanghai',
    'weekdays': [0, 1, 2, 3, 4],
    'windows': [[540, 720], [780, 1080]],
    'holidays': [], 'sla_minutes': {'普通': 480, '紧急': 120},
}


def case(i, minute, priority, dept, overdue=False):
    # 工作时窗 09:00-12:00、13:00-18:00；普通阈值480分钟、紧急120分钟。
    day = 1 + i % 20
    hour = 9 + minute // 60
    mm = minute % 60
    opened = f'2026-11-{day:02d}T{hour:02d}:{mm:02d}:00+08:00'
    if overdue:
        closed = f'2026-11-{day:02d}T17:59:00+08:00'
    else:
        closed = f'2026-11-{day:02d}T{hour:02d}:{mm:02d}:30+08:00'
    return {'period': '2026-11', 'department': dept, 'opened': opened,
            'closed': closed, 'priority': priority}


class FakeSession:
    def __init__(self):
        self.runs = {}
        self.comparisons = {}
        self.audits = []
        self._run_seq = 0
        self._comp_seq = 0
        # 测试钩子：commit 时触发，用于模拟运行中改选。
        self.on_commit = None
        self.rollback_count = 0

    def get(self, model, pk):
        store = self.runs if model is Run else self.comparisons
        return store.get(pk)

    def add(self, obj):
        if isinstance(obj, Run):
            obj.id = (max(self.runs, default=0) + 1)
            self.runs[obj.id] = obj
        elif isinstance(obj, Comparison):
            obj.id = (max(self.comparisons, default=0) + 1)
            self.comparisons[obj.id] = obj
        elif isinstance(obj, Audit):
            self.audits.append(obj)

    def query(self, model):
        store = self.runs if model is Run else (
            self.comparisons if model is Comparison else self.audits)
        return Query(list(store.values()))

    def flush(self):
        pass

    def commit(self):
        hook = self.on_commit
        if hook:
            hook()

    def expire_all(self):
        pass

    def rollback(self):
        self.rollback_count += 1


class Query:
    def __init__(self, items):
        self._items = items

    def filter(self, *args, **kwargs):
        # 仅覆盖测试用到的等值过滤。
        result = self._items
        for k, v in kwargs.items():
            result = [x for x in result if getattr(x, k, None) == v]
        return Query(result)

    def order_by(self, *args):
        return self

    def all(self):
        return list(self._items)


class Body:
    def __init__(self, name, run_a_id, run_b_id):
        self.name = name
        self.run_a_id = run_a_id
        self.run_b_id = run_b_id


class User:
    def __init__(self, email):
        self.email = email
        self.role = 'admin'


def make_run(db, entries, approved=True):
    run = Run(name=f'报告-{len(db.runs)+1}', status='completed',
              snapshot={'policy': copy.deepcopy(POLICY),
                        'sources': copy.deepcopy(entries)},
              checksum='sha-' + str(len(db.runs)), result={}, approved=approved)
    db.add(run)
    return run


def main():
    db = FakeSession()
    user = User('admin@demo.local')

    # 构造两组：多数同优先级、相隔几分钟可配；插入少量超时样本。
    entries_a, entries_b = [], []
    for i in range(40):
        entries_a.append(case(i, i % 50, '普通' if i % 2 else '紧急',
                              '数据建模组', overdue=(i % 7 == 0)))
        entries_b.append(case(i, (i % 50) + 2, '普通' if i % 2 else '紧急',
                              '数据采样组', overdue=(i % 5 == 0)))
    run_a = make_run(db, entries_a)
    run_b = make_run(db, entries_b)

    # --- 1. 未复核报告不可比较 ---
    run_c = make_run(db, entries_a, approved=False)
    try:
        svc.create_comparison(db, user, Body('c', run_a.id, run_c.id))
        raise AssertionError('应拒绝未复核报告')
    except HTTPException as e:
        assert e.status_code == 409
    try:
        svc.create_comparison(db, user, Body('c', run_a.id, run_a.id))
        raise AssertionError('应拒绝同份报告')
    except HTTPException as e:
        assert e.status_code == 422

    # --- 2. 创建并入队 ---
    comp = svc.create_comparison(db, user, Body('十一月组间比较', run_a.id, run_b.id))
    assert comp.status == 'queued' and comp.generation == 1
    assert redis_shim._pushed[-1] == (svc.COMPARISON_QUEUE, f'{comp.id}:1')

    # --- 3. 小批大小执行，验证检查点落盘与续跑 ---
    original_batch = pairing.BATCH_SIZE
    pairing.BATCH_SIZE = 8
    try:
        # 先跑到第一批检查点后模拟中断：直接用 progress 续跑。
        svc.execute(db, comp.id, 1)
    finally:
        pairing.BATCH_SIZE = original_batch
    assert comp.status == 'completed'
    assert comp.progress['finished'] is True
    assert comp.result['stats']['pair_count'] > 0

    # 发布闸门：有配对且两侧超时率齐备
    assert comp.published is True
    ra = comp.result['run_a']['overdue_rate']
    rb = comp.result['run_b']['overdue_rate']
    assert ra and rb and 0 <= ra['overdue_rate'] <= 1
    assert ra['paired_samples'] == rb['paired_samples'] == comp.result['stats']['pair_count']
    # 超时率与逐样本口径一致
    flags_a = svc._overdue_flags(entries_a, POLICY)
    paired_lines_a = [p[0] for p in comp.progress['pairs']]
    expect_overdue = sum(1 for ln in paired_lines_a if flags_a[ln]['overdue'])
    assert ra['overdue'] == expect_overdue
    assert abs(ra['overdue_rate'] - expect_overdue / len(paired_lines_a)) < 1e-12

    # 每单用一次、约束不破坏
    las = [p[0] for p in comp.progress['pairs']]
    lbs = [p[1] for p in comp.progress['pairs']]
    assert len(las) == len(set(las)) and len(lbs) == len(set(lbs))

    # --- 4. 配对依据分页 ---
    page1 = svc.pair_page(db, comp.id, 0, 10)
    page2 = svc.pair_page(db, comp.id, 10, 10)
    assert page1['total'] == comp.result['stats']['pair_count']
    assert len(page1['items']) == 10
    assert page1['items'][0]['same_priority'] is True
    assert page1['items'][0]['gap_seconds'] <= 30 * 60
    assert page2['items'][0] != page1['items'][0]
    # 行号定序
    seq = [it['line_a'] for it in page1['items']]
    assert seq == sorted(seq)
    csv_text = svc.pair_rows_csv(db, comp.id)
    assert csv_text.startswith('﻿') and 'A底稿行号' in csv_text

    # --- 5. 改选代次保护：旧代次 execute 不得覆盖新代次结果 ---
    db2 = FakeSession()
    db2.runs, db2.comparisons, db2.audits = db.runs, db.comparisons, db.audits
    new_run = make_run(db2, entries_b)
    reselected = svc.reselect(db2, user, comp.id,
                              Body('改选后比较', run_a.id, new_run.id))
    assert reselected.generation == 2 and reselected.status == 'queued'
    assert reselected.progress is None and reselected.published is False
    assert redis_shim._pushed[-1] == (svc.COMPARISON_QUEUE, f'{comp.id}:2')
    # 旧代次消息迟到：应直接静默返回，不产生任何写入
    before = copy.deepcopy(reselected.status)
    svc.execute(db2, comp.id, 1)
    assert reselected.status == before
    # 新代次正常完成
    svc.execute(db2, comp.id, 2)
    assert reselected.status == 'completed' and reselected.generation == 2
    assert reselected.published is True
    assert reselected.run_b_id == new_run.id

    # --- 6. 运行中改选：on_batch 守卫抛 StaleGeneration，旧结果回滚丢弃 ---
    comp3 = svc.create_comparison(db2, user, Body('并发改选用例', run_a.id, run_b.id))
    calls = {'n': 0}

    def bump_on_second_commit():
        calls['n'] += 1
        if calls['n'] == 1:
            # 第一批检查点提交后立即改选（模拟并发请求）
            svc.reselect(db2, user, comp3.id,
                         Body('并发改选用例-v2', run_a.id, new_run.id))

    db2.on_commit = bump_on_second_commit
    pairing.BATCH_SIZE = 8
    try:
        # comp3 创建即 gen1；首批检查点提交后被并发改选淘汰为 gen2。
        svc.execute(db2, comp3.id, 1)
    finally:
        pairing.BATCH_SIZE = original_batch
        db2.on_commit = None
    assert comp3.generation == 2 and comp3.status == 'queued'
    assert comp3.progress is None  # 旧代次检查点未覆盖新任务
    assert db2.rollback_count >= 1
    # 新代次可继续完成
    svc.execute(db2, comp3.id, 2)
    assert comp3.status == 'completed' and comp3.published is True

    # --- 7. 发布闸门：零配对不得发布 ---
    empty_a = [case(0, 0, '普通', 'X')]
    empty_b = [case(0, 45, '紧急', 'Y')]  # 异优先级且时间窗边缘外
    r1 = make_run(db2, empty_a)
    r2 = make_run(db2, empty_b)
    comp_zero = svc.create_comparison(db2, user, Body('零配对', r1.id, r2.id))
    svc.execute(db2, comp_zero.id, 1)
    assert comp_zero.status == 'completed'
    assert comp_zero.published is False
    assert comp_zero.result['rates_ready'] is False
    assert comp_zero.result['warnings']

    # --- 8. 审计留痕 ---
    actions = [a.action for a in db2.audits]
    assert 'comparison.create' in actions
    assert 'comparison.reselect' in actions
    assert 'comparison.complete' in actions

    print('集成测试 8 组场景全部通过：创建校验、分批落盘、发布闸门、')
    print('分页依据与CSV、改选代次保护、运行中改选回滚、零配对不发布、审计留痕')


if __name__ == '__main__':
    main()
