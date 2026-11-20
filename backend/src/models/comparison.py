from sqlalchemy import Column,Integer,String,JSON,Text,Float,Boolean
from src.config.database import Base
class Comparison(Base):
 """两组已复核报告冻结底稿的配对比较。"""
 __tablename__='comparisons'
 id=Column(Integer,primary_key=True)
 name=Column(String(120))
 run_a_id=Column(Integer)
 run_b_id=Column(Integer)
 status=Column(String(20),default='queued')
 generation=Column(Integer,default=1)
 progress=Column(JSON)
 result=Column(JSON)
 error=Column(Text)
 published=Column(Boolean,default=False)
 created=Column(Float)
