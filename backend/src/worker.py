import time,json,traceback
from pathlib import Path
import redis
from src.config.database import Base,engine,Session
from src.config.settings import REDIS_URL
from src.models import identity,source,run,comparison
from src.models.run import Run
from src.models.comparison import Comparison
from src.services.calculation import calculate
from src.services.delivery import tick
from src.services import comparison as comparison_service
Base.metadata.create_all(engine)
r=redis.Redis.from_url(REDIS_URL)

def requeue_pending():
 """启动恢复：中断在 running 的任务回到 queued 并按当前代次重新入队。

 报告任务与配对比较分别处理；比较任务的检查点保留在 progress 中，
 续跑沿用原底稿与已完成批次，旧代次消息到达时会被代次校验直接丢弃。
 """
 with Session() as db:
  for job in db.query(Run).filter(Run.status=='running').all():
   job.status='queued';r.rpush('report-jobs',job.id)
  for comp in db.query(Comparison).filter(Comparison.status.in_(('running','queued'))).all():
   comp.status='queued';r.rpush(comparison_service.COMPARISON_QUEUE,f'{comp.id}:{comp.generation}')
  db.commit()
requeue_pending()

def handle_comparison(item):
 try:
  comp_id,generation=item.decode().split(':',1)
  comp_id,generation=int(comp_id),int(generation)
 except Exception:
  return
 try:
  with Session() as db:
   comparison_service.execute(db,comp_id,generation)
 except Exception:
  # 同代次失败已在 service 内落库；代次淘汰为预期分支，仅记录堆栈。
  traceback.print_exc()

while True:
 Path('/tmp/worker-heartbeat').write_text(str(time.time()))
 try:
  item=r.lpop('report-jobs')
  if item:
   with Session() as db:
    job=db.get(Run,int(item))
    if job and job.status=='queued':
     job.status='running';db.commit()
     try: job.result=calculate(job.snapshot['sources'],job.snapshot['policy']);job.status='completed'
     except Exception as exc: job.status='failed';job.error=str(exc);traceback.print_exc()
     db.commit()
  comp_item=r.lpop(comparison_service.COMPARISON_QUEUE)
  if comp_item: handle_comparison(comp_item)
  with Session() as db: tick(db)
 except Exception: traceback.print_exc()
 time.sleep(1)
