"""无第三方依赖环境下的最小垫片：让服务模块可在 stdlib 中导入。

仅用于 tests/ 下的集成测试；生产仍使用真实 FastAPI/SQLAlchemy/Redis。
"""
import json
import sys
import types
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]


def install():
    # ---- fastapi 替身 ----
    fastapi = types.ModuleType('fastapi')

    class HTTPException(Exception):
        def __init__(self, status_code, detail):
            super().__init__(detail)
            self.status_code = status_code
            self.detail = detail

    fastapi.HTTPException = HTTPException
    responses = types.ModuleType('fastapi.responses')

    class Response:
        def __init__(self, content='', media_type=None, headers=None):
            self.body = content
            self.media_type = media_type
            self.headers = headers or {}

    responses.Response = Response
    fastapi.responses = responses
    sys.modules.setdefault('fastapi', fastapi)
    sys.modules.setdefault('fastapi.responses', responses)

    # ---- sqlalchemy 替身：列声明忽略，模型退化为普通类 ----
    sa = types.ModuleType('sqlalchemy')

    def _column(*args, **kwargs):
        return None

    for name in ('Column', 'Integer', 'String', 'JSON', 'Text', 'Float',
                 'Boolean'):
        setattr(sa, name, _column)

    class _Base:
        pass

    sa.create_engine = lambda *a, **k: None
    sa.orm = types.ModuleType('sqlalchemy.orm')

    def sessionmaker(*a, **k):
        return None

    class DeclarativeBase(_Base):
        def __init__(self, **kwargs):
            for key, value in kwargs.items():
                setattr(self, key, value)

    sa.orm.sessionmaker = sessionmaker
    sa.orm.DeclarativeBase = DeclarativeBase
    sys.modules.setdefault('sqlalchemy', sa)
    sys.modules.setdefault('sqlalchemy.orm', sa.orm)

    # ---- 配置替身（读取真实 project.json）----
    config = json.loads((BACKEND / 'project.json').read_text(encoding='utf-8'))
    settings = types.ModuleType('src.config.settings')
    settings.CONFIG = config
    settings.DATABASE_URL = 'sqlite://'
    settings.REDIS_URL = 'redis://test'
    settings.SECRET = 'test-secret'
    sys.modules.setdefault('src.config.settings', settings)

    # ---- redis 替身：记录入队消息 ----
    redis_mod = types.ModuleType('redis')
    pushed = []

    class _Redis:
        def rpush(self, queue, value):
            pushed.append((queue, str(value)))
            return len(pushed)

        def ping(self):
            return True

    redis_mod.Redis = types.SimpleNamespace(from_url=lambda url: _Redis())
    redis_mod._pushed = pushed
    sys.modules.setdefault('redis', redis_mod)

    return redis_mod
