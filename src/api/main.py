import asyncio
from contextlib import asynccontextmanager, suppress
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from src.api.tasks import router as tasks_router
from src.api.healthcheck import router as healthcheck_router
from src.api.entities import router as entities_router
from src.api.review import router as review_router
from src.api.dashboard import router as dashboard_router
from src.api.auth import router as auth_router
from src.api.stream import router as stream_router
from src.api.admin import router as admin_router
from src.api.prompts import router as prompts_router
from src.api.activity import router as activity_router
from src.api.meta import router as meta_router
from src.api.internal import router as internal_router
from src.api.styles import router as styles_router
from src.api.system import router as system_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    from src.stream.scheduler import scheduler
    from src.stream.maintenance import cycle
    from src.stream.progress import progress
    from src.review.heartbeat import heartbeat_loop
    from src.api.system import load_settings_from_db
    await load_settings_from_db()   # 系统参数 web 化：启动加载覆盖 .env 默认
    await progress.start()
    await scheduler.start()
    await cycle.start()
    heartbeat_task = asyncio.create_task(heartbeat_loop())
    yield
    heartbeat_task.cancel()
    with suppress(asyncio.CancelledError):
        await heartbeat_task
    await cycle.stop()
    await scheduler.stop()
    await progress.stop()


app = FastAPI(title="query-validation-platform", lifespan=lifespan)
app.include_router(tasks_router)
app.include_router(healthcheck_router)
app.include_router(entities_router)
app.include_router(review_router)
app.include_router(dashboard_router)
app.include_router(auth_router)
app.include_router(stream_router)
app.include_router(admin_router)
app.include_router(prompts_router)
app.include_router(activity_router)
app.include_router(meta_router)
app.include_router(internal_router)
app.include_router(styles_router)
app.include_router(system_router)

# 静态前端（审核工作台 + 看板）
STATIC_DIR = Path(__file__).resolve().parent.parent.parent / "static"
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


class _StaticNoCacheASGI:
    """静态资源与 SPA 入口禁用启发式缓存：每次带 etag revalidate（未变则 304），
    避免改版后浏览器长期沿用旧 JS（曾导致任务中心批量删除修复不生效）。

    必须用纯 ASGI 中间件而非 @app.middleware("http")（BaseHTTPMiddleware）：
    后者会桥接转发响应体，对 /api/stream/events 这类 StreamingResponse 有
    「首帧后流冻结」缺陷（2026-09-01 排查：监控页事件流/Agent数据流静止的
    传输层根因）。纯 ASGI 只在响应头阶段追加 header，不触碰响应体。
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            path = scope.get("path", "")
            if path == "/" or path.startswith("/static"):
                async def _send(message):
                    if message["type"] == "http.response.start":
                        message.setdefault("headers", []).append(
                            (b"cache-control", b"no-cache"))
                    await send(message)
                await self.app(scope, receive, _send)
                return
        await self.app(scope, receive, send)


app.add_middleware(_StaticNoCacheASGI)


# SPA 入口（static/index.html + hash 路由）
@app.get("/")
async def index():
    return FileResponse(str(STATIC_DIR / "index.html"), headers={"Cache-Control": "no-cache"})


# 旧页面路径 → SPA hash 路由（兼容旧链接/书签）
_LEGACY_ROUTES = {
    "/login": "/#/login",
    "/workbench": "/#/review",
    "/dashboard": "/#/",
    "/import": "/#/import",
    "/progress": "/#/",
    "/stream": "/#/tasks",
    "/sample": "/#/sample",
    "/admin": "/#/admin",
}

for _path, _target in _LEGACY_ROUTES.items():
    app.add_api_route(_path, lambda t=_target: RedirectResponse(url=t), methods=["GET"])
