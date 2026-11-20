from fastapi import APIRouter,Depends,HTTPException,Query
from fastapi.responses import Response
from src.config.database import database
from src.controllers.auth import current,editor
from src.models.comparison import Comparison
from src.schemas.requests import ComparisonInput
from src.services import comparison as service
from src.utils.serialize import row
router=APIRouter()

def _visible(obj,user):
 # 业务阅读者仅可见已发布比较；负责人与分析师可见全部。
 if user.role=='viewer' and not obj.published:
  raise HTTPException(403,'业务阅读者仅可查看已发布的配对比较')
 return row(obj)

@router.get('/comparisons')
def list_comparisons(db=Depends(database),u=Depends(current)):
 items=db.query(Comparison).order_by(Comparison.id.desc()).all()
 return [row(x) for x in items if u.role!='viewer' or x.published]

@router.post('/comparisons')
def create(b:ComparisonInput,db=Depends(database),u=Depends(editor)):
 return row(service.create_comparison(db,u,b))

@router.get('/comparisons/{id}')
def detail(id:int,db=Depends(database),u=Depends(current)):
 obj=db.get(Comparison,id)
 if not obj: raise HTTPException(404,'比较任务不存在')
 return _visible(obj,u)

@router.put('/comparisons/{id}')
def reselect(id:int,b:ComparisonInput,db=Depends(database),u=Depends(editor)):
 return row(service.reselect(db,u,id,b))

@router.get('/comparisons/{id}/pairs')
def pairs(id:int,offset:int=Query(0,ge=0),limit:int=Query(10,ge=1,le=500),
          db=Depends(database),u=Depends(current)):
 obj=db.get(Comparison,id)
 if not obj: raise HTTPException(404,'比较任务不存在')
 _visible(obj,u)
 return service.pair_page(db,id,offset,limit)

@router.get('/comparisons/{id}/csv')
def download(id:int,db=Depends(database),u=Depends(current)):
 obj=db.get(Comparison,id)
 if not obj: raise HTTPException(404,'比较任务不存在')
 _visible(obj,u)
 return Response(service.pair_rows_csv(db,id),media_type='text/csv',
  headers={'Content-Disposition':f'attachment; filename="comparison-{id}.csv"'})
