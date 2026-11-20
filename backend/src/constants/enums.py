ROLES=('admin','analyst','viewer')
STATUSES=('queued','running','completed','failed')
# 配对比较沿用同一状态机；发布为独立 published 标记，不混入 status。
COMPARISON_STATUSES=STATUSES
