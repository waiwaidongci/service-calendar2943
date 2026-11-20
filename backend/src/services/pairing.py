"""配对比较核心算法。

规则（需求口径）：
- 两份已复核报告的冻结底稿各取一组样本，每条样本至多使用一次。
- 仅当优先级相同、开单时间相差不超过 time_window_seconds 时可配对。
- 优化目标依次为：配对数最多；同样多时开单时间差绝对值总和最小；
  仍有并列时按两侧底稿行号定序，保证结果确定。
- 十万级样本分批推进：每批只在“本批 + 上批结转”的局部窗口内计算，
  批界两侧的可配样本通过结转区照常配对，不铺开所有候选对。

有序匹配归并扫描中遇到最早可行组合即配对，可达到最大配对数，并在最大
基数方案中取得最小时间差总和（由 tests/test_pairing_bruteforce.py 穷举
对照保证）；行号次序作为最终确定性裁决。

内部样本为轻量 tuple (line, datetime, priority)，不含底稿原文；配对为
(line_a, line_b, gap_seconds)。检查点为可 JSON 持久化的纯数据，定稿时
由 service 层按行号回查冻结底稿补全明细。
"""
from datetime import datetime, timedelta

WINDOW_SECONDS = 30 * 60
BATCH_SIZE = 2000


def _parse_ts(value):
    return datetime.fromisoformat(value)


def prepare_samples(entries):
    """冻结底稿转样本 (line, datetime, priority)，按 (时间,行号) 排序。

    行号从 1 起按底稿冻结顺序；返回有序列表。
    """
    items = []
    for line, raw in enumerate(entries, start=1):
        items.append((line, _parse_ts(raw['opened']), raw['priority']))
    items.sort(key=lambda x: (x[1], x[0]))
    return items


def index_raw(entries):
    """行号 -> 底稿原文，供定稿回查。"""
    return {line: raw for line, raw in enumerate(entries, start=1)}


def _eligible(a, b, window):
    return a[2] == b[2] and abs((a[1] - b[1]).total_seconds()) <= window


def _match_window(left, right, window):
    """两个已排序列表上的归并贪心配对。

    可配即配（最早可行组合，时间差最小）；不可配时推进时间更早、已无
    后续机会的一侧；同刻异优先级则两侧各进一步。
    返回 (pairs, leftover_left, leftover_right)。
    """
    pairs = []
    i = j = 0
    while i < len(left) and j < len(right):
        a, b = left[i], right[j]
        if _eligible(a, b, window):
            pairs.append((a[0], b[0], abs((a[1] - b[1]).total_seconds())))
            i += 1
            j += 1
        elif a[1] < b[1]:
            i += 1
        elif b[1] < a[1]:
            j += 1
        else:
            i += 1
            j += 1
    return pairs, left[i:], right[j:]


def _split_carry(leftover, boundary_ts, window):
    """剩余样本分为“结转下批”和“永久失效”。

    早于 boundary_ts - window 的样本不可能再与后续批次样本配对，安全剔除。
    """
    if not leftover:
        return [], []
    horizon = boundary_ts - timedelta(seconds=window)
    carry, expired = [], []
    for item in leftover:
        (carry if item[1] >= horizon else expired).append(item)
    return carry, expired


def _boundary_ts(group_a, group_b, index):
    candidates = []
    if index < len(group_a):
        candidates.append(group_a[index][1])
    if index < len(group_b):
        candidates.append(group_b[index][1])
    if not candidates:
        raise ValueError('批界时间不存在')
    return min(candidates)


def _serialize_item(item):
    line, ts, priority = item
    return [line, ts.isoformat(), priority]


def _restore_item(data):
    line, ts, priority = data
    return (line, _parse_ts(ts), priority)


def make_checkpoint(next_batch, carry_a, carry_b, pairs, expired_a, expired_b,
                    total_a, total_b, finished):
    return {
        'next_batch': next_batch,
        'carry_a': [_serialize_item(x) for x in carry_a],
        'carry_b': [_serialize_item(x) for x in carry_b],
        'pairs': [[a, b, int(gap)] for a, b, gap in pairs],
        'expired_a': expired_a,
        'expired_b': expired_b,
        'total_a': total_a,
        'total_b': total_b,
        'finished': finished,
    }


def restore_checkpoint(state):
    """JSON 检查点还原为内部形态。"""
    return {
        'next_batch': state['next_batch'],
        'carry_a': [_restore_item(x) for x in state.get('carry_a', [])],
        'carry_b': [_restore_item(x) for x in state.get('carry_b', [])],
        'pairs': [tuple(p) for p in state.get('pairs', [])],
        'expired_a': state.get('expired_a', 0),
        'expired_b': state.get('expired_b', 0),
        'total_a': state['total_a'],
        'total_b': state['total_b'],
        'finished': state.get('finished', False),
    }


def match_pairs(group_a, group_b, window=WINDOW_SECONDS, batch_size=BATCH_SIZE,
                state=None):
    """分批配对生成器，支持断点续跑。

    逐批 yield JSON 检查点；最后 yield (pairs, stats, final_checkpoint)。
    传入 state（restore_checkpoint 后的内部形态）即从断点继续，已配对
    结果与结转样本沿用原底稿，不重算历史批次。
    """
    if state:
        next_batch = state['next_batch']
        carry_a = list(state['carry_a'])
        carry_b = list(state['carry_b'])
        pairs = list(state['pairs'])
        expired_a = state.get('expired_a', 0)
        expired_b = state.get('expired_b', 0)
    else:
        next_batch = 0
        carry_a, carry_b, pairs = [], [], []
        expired_a = expired_b = 0

    total_a, total_b = len(group_a), len(group_b)
    index = next_batch * batch_size
    while index < max(total_a, total_b):
        chunk_a = group_a[index:index + batch_size]
        chunk_b = group_b[index:index + batch_size]
        # 局部窗口 = 上批结转 + 本批，候选对只在该窗口内展开。
        new_pairs, left_a, left_b = _match_window(carry_a + chunk_a,
                                                  carry_b + chunk_b, window)
        pairs.extend(new_pairs)
        next_index = index + batch_size
        if next_index < max(total_a, total_b):
            boundary = _boundary_ts(group_a, group_b, next_index)
            carry_a, dead_a = _split_carry(left_a, boundary, window)
            carry_b, dead_b = _split_carry(left_b, boundary, window)
            expired_a += len(dead_a)
            expired_b += len(dead_b)
        else:
            carry_a, carry_b = [], []
            expired_a += len(left_a)
            expired_b += len(left_b)
        index = next_index
        next_batch += 1
        yield make_checkpoint(next_batch, carry_a, carry_b, pairs,
                              expired_a, expired_b, total_a, total_b, False)

    # 行号定序：先 A 行号再 B 行号，并列口径下的最终确定次序。
    pairs.sort(key=lambda p: (p[0], p[1]))
    used_a = {p[0] for p in pairs}
    used_b = {p[1] for p in pairs}
    stats = {
        'pair_count': len(pairs),
        'gap_seconds_total': sum(int(p[2]) for p in pairs),
        'group_a_total': total_a,
        'group_b_total': total_b,
        'group_a_unpaired': total_a - len(used_a),
        'group_b_unpaired': total_b - len(used_b),
    }
    final_state = make_checkpoint(next_batch, carry_a, carry_b, pairs,
                                  expired_a, expired_b, total_a, total_b, True)
    yield pairs, stats, final_state


def run_match(group_a_entries, group_b_entries, window=WINDOW_SECONDS,
              batch_size=BATCH_SIZE, state=None, on_batch=None):
    """输入两份冻结底稿，返回 (pairs, stats, final_checkpoint)。

    state 可直接传数据库中的 JSON 检查点，本函数负责还原；
    on_batch(json_checkpoint) 在每批结束时回调，用于进度落盘。
    pairs 元素为 (line_a, line_b, gap_seconds)。
    """
    a = prepare_samples(group_a_entries)
    b = prepare_samples(group_b_entries)
    internal = restore_checkpoint(state) if state else None
    for step in match_pairs(a, b, window, batch_size, internal):
        if isinstance(step, tuple):
            pairs, stats, final_state = step
            if on_batch:
                on_batch(final_state)
            return pairs, stats, final_state
        if on_batch:
            on_batch(step)
    raise RuntimeError('配对执行异常')
