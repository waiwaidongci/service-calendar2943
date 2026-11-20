"""配对比较服务。

数据来源严格限定为两份“已复核报告”的冻结底稿 snapshot['sources']，
冻结口径取自各自 snapshot['policy']；已复核报告不可改写，因此比较
全程引用不可变快照，与正式报告链路隔离。

并发与续跑：
- 每次创建/改选递增 generation 并入队；worker 每批写检查点前后核对
  generation，代次不符立即放弃，旧计算不覆盖新结果。
- 进度 progress 落库（批序号、结转区、已配对行号），中断后由同代次
  任务从断点续跑，沿用原底稿与已做选择，不重算历史批次。
- 发布闸门：配对完成且两组在配对样本中的超时率齐备（pair_count>0）
  才置 published=True。
"""
import time
from fastapi import HTTPException
from src.models.run import Run
from src.models.comparison import Comparison
from src.services import pairing
from src.services.pairing import WINDOW_SECONDS
from src.services.calculation import business_minutes
from src.services.audit import log
from src.config.settings import REDIS_URL
import redis

COMPARISON_QUEUE = 'comparison-jobs'
PAGE_SIZE = 10


class StaleGeneration(Exception):
    """代次已被改选淘汰，当前计算结果必须丢弃。"""


def _load_runs(db, run_a_id, run_b_id):
    if run_a_id == run_b_id:
        raise HTTPException(422, '配对比较需选择两份不同报告')
    run_a = db.get(Run, run_a_id)
    run_b = db.get(Run, run_b_id)
    if not run_a or not run_b:
        raise HTTPException(404, '报告不存在')
    for r in (run_a, run_b):
        if r.status != 'completed':
            raise HTTPException(409, '仅可比较已完成报告：' + r.name)
        if not r.approved:
            raise HTTPException(409, '仅可比较已复核报告：' + r.name)
    return run_a, run_b


def create_comparison(db, user, body):
    run_a, run_b = _load_runs(db, body.run_a_id, body.run_b_id)
    obj = Comparison(
        name=body.name, run_a_id=run_a.id, run_b_id=run_b.id,
        status='queued', generation=1, progress=None, result=None,
        error=None, published=False, created=time.time(),
    )
    db.add(obj)
    db.flush()
    log(db, user.email, 'comparison.create', f'{obj.id}:{body.name}')
    db.commit()
    redis.Redis.from_url(REDIS_URL).rpush(COMPARISON_QUEUE, f'{obj.id}:1')
    return obj


def reselect(db, user, comparison_id, body):
    """改选报告：代次 +1、清空旧进度与结果并重新排队。"""
    obj = db.get(Comparison, comparison_id)
    if not obj:
        raise HTTPException(404, '比较任务不存在')
    _load_runs(db, body.run_a_id, body.run_b_id)
    obj.name = body.name
    obj.run_a_id = body.run_a_id
    obj.run_b_id = body.run_b_id
    obj.generation += 1
    obj.status = 'queued'
    obj.progress = None
    obj.result = None
    obj.error = None
    obj.published = False
    log(db, user.email, 'comparison.reselect',
        f'{obj.id}:gen{obj.generation}')
    db.commit()
    redis.Redis.from_url(REDIS_URL).rpush(
        COMPARISON_QUEUE, f'{obj.id}:{obj.generation}')
    return obj


def _overdue_flags(entries, policy):
    """逐样本超时标记（行号从 1 起），沿用报告冻结口径。"""
    flags = {}
    for line, raw in enumerate(entries, start=1):
        limit = policy['sla_minutes'][raw['priority']]
        mins = business_minutes(raw['opened'], raw['closed'], policy)
        flags[line] = {
            'overdue': mins > limit,
            'business_minutes': mins,
            'sla_minutes': limit,
        }
    return flags


def _overdue_rate(flags, lines):
    if not lines:
        return None
    total = len(lines)
    overdue = sum(1 for ln in lines if flags[ln]['overdue'])
    return {
        'paired_samples': total,
        'overdue': overdue,
        'overdue_rate': overdue / total,
    }


def _build_result(run_a, run_b, pairs, stats, overdue_a, overdue_b):
    lines_a = [p[0] for p in pairs]
    lines_b = [p[1] for p in pairs]
    rate_a = _overdue_rate(overdue_a, lines_a)
    rate_b = _overdue_rate(overdue_b, lines_b)
    # 两侧配对样本超时率齐备：均有配对样本、逐笔超时核算完成且数量一致。
    rates_ready = (
        rate_a is not None and rate_b is not None
        and rate_a['paired_samples'] == len(pairs) > 0
        and rate_b['paired_samples'] == len(pairs)
    )
    warnings = []
    if stats['pair_count'] == 0:
        warnings.append('两份底稿在同优先级与30分钟时间窗内没有可配样本，未发布。')
    if stats['group_a_unpaired'] or stats['group_b_unpaired']:
        warnings.append(
            f"未参与配对样本：报告A {stats['group_a_unpaired']} 条、"
            f"报告B {stats['group_b_unpaired']} 条，不计入配对超时率。")
    return {
        'window_seconds': WINDOW_SECONDS,
        'basis': (
            '仅同优先级且开单时间相差不超过30分钟可配对；每条样本只用一次；'
            '先最大化配对数，并列时取开单时间差总和最小，再按底稿行号定序。'
            '分批推进，批界两侧经结转区照常配对。'
        ),
        'run_a': {'id': run_a.id, 'name': run_a.name,
                  'checksum': run_a.checksum,
                  'policy_version': run_a.snapshot['policy']['version'],
                  'overdue_rate': rate_a},
        'run_b': {'id': run_b.id, 'name': run_b.name,
                  'checksum': run_b.checksum,
                  'policy_version': run_b.snapshot['policy']['version'],
                  'overdue_rate': rate_b},
        'rates_ready': rates_ready,
        'stats': stats,
        'warnings': warnings,
    }


def execute(db, comparison_id, generation):
    """worker 调用：代次感知的分批配对，完成后通过发布闸门。"""
    obj = db.get(Comparison, comparison_id)
    if not obj or obj.generation != generation or obj.status != 'queued':
        return
    run_a = db.get(Run, obj.run_a_id)
    run_b = db.get(Run, obj.run_b_id)
    if not run_a or not run_b or not run_a.approved or not run_b.approved:
        obj.status = 'failed'
        obj.error = '引用报告缺失或未复核，拒绝计算'
        db.commit()
        return
    obj.status = 'running'
    obj.error = None
    db.commit()

    entries_a = run_a.snapshot['sources']
    entries_b = run_b.snapshot['sources']
    resume_state = obj.progress if obj.progress and not obj.progress.get('finished') else None

    def guard(cp):
        db.expire_all()
        fresh = db.get(Comparison, comparison_id)
        if not fresh or fresh.generation != generation:
            raise StaleGeneration(comparison_id)
        fresh.progress = cp
        db.commit()

    try:
        pairs, stats, final_cp = pairing.run_match(
            entries_a, entries_b, state=resume_state, on_batch=guard)
        overdue_a = _overdue_flags(entries_a, run_a.snapshot['policy'])
        overdue_b = _overdue_flags(entries_b, run_b.snapshot['policy'])
        result = _build_result(run_a, run_b, pairs, stats, overdue_a, overdue_b)
        db.expire_all()
        fresh = db.get(Comparison, comparison_id)
        if not fresh or fresh.generation != generation:
            raise StaleGeneration(comparison_id)
        fresh.progress = final_cp
        fresh.result = result
        fresh.status = 'completed'
        # 发布闸门：配对与两组配对样本超时率齐备方可发布。
        fresh.published = bool(stats['pair_count'] > 0 and result['rates_ready'])
        log(db, 'worker', 'comparison.complete',
            f'{comparison_id}:gen{generation}:published={fresh.published}')
        db.commit()
    except StaleGeneration:
        # 改选已淘汰本代次：回滚本会话内未提交写入，旧结果不得覆盖新任务。
        db.rollback()
    except Exception as exc:
        db.rollback()
        fresh = db.get(Comparison, comparison_id)
        if fresh and fresh.generation == generation:
            fresh.status = 'failed'
            fresh.error = str(exc)[:1000]
            db.commit()
        raise


def pair_page(db, comparison_id, offset, limit=PAGE_SIZE):
    """配对依据分页：按定稿行号次序返回配对明细。"""
    obj = db.get(Comparison, comparison_id)
    if not obj:
        raise HTTPException(404, '比较任务不存在')
    if obj.status != 'completed' or not obj.progress or not obj.progress.get('finished'):
        raise HTTPException(409, '比较尚未完成')
    run_a = db.get(Run, obj.run_a_id)
    run_b = db.get(Run, obj.run_b_id)
    raw_a = pairing.index_raw(run_a.snapshot['sources'])
    raw_b = pairing.index_raw(run_b.snapshot['sources'])
    all_pairs = [tuple(p) for p in obj.progress['pairs']]
    total = len(all_pairs)
    page = all_pairs[offset:offset + limit]
    items = []
    for la, lb, gap in page:
        ra, rb = raw_a[la], raw_b[lb]
        items.append({
            'line_a': la, 'line_b': lb, 'gap_seconds': gap,
            'same_priority': ra['priority'] == rb['priority'],
            'a': ra, 'b': rb,
        })
    return {'total': total, 'offset': offset, 'limit': limit, 'items': items}


def pair_rows_csv(db, comparison_id):
    """全量配对依据 CSV（复用分页逻辑逐页取数，避免一次性大对象）。"""
    import csv
    import io
    obj = db.get(Comparison, comparison_id)
    if not obj or obj.status != 'completed':
        raise HTTPException(409, '比较尚未完成')
    run_a = db.get(Run, obj.run_a_id)
    run_b = db.get(Run, obj.run_b_id)
    raw_a = pairing.index_raw(run_a.snapshot['sources'])
    raw_b = pairing.index_raw(run_b.snapshot['sources'])
    stream = io.StringIO()
    cols = ['A底稿行号', 'A开单时间', 'A优先级', 'A小组',
            'B底稿行号', 'B开单时间', 'B优先级', 'B小组', '开单时间差秒']
    w = csv.DictWriter(stream, fieldnames=cols)
    w.writeheader()
    for la, lb, gap in (tuple(p) for p in obj.progress['pairs']):
        ra, rb = raw_a[la], raw_b[lb]
        w.writerow({
            'A底稿行号': la, 'A开单时间': ra['opened'], 'A优先级': ra['priority'],
            'A小组': ra['department'], 'B底稿行号': lb, 'B开单时间': rb['opened'],
            'B优先级': rb['priority'], 'B小组': rb['department'],
            '开单时间差秒': int(gap),
        })
    return '﻿' + stream.getvalue()
