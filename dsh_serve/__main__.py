"""入口：python -m dsh_serve（读环境变量起 uvicorn）。

SIGTERM/SIGINT 由 uvicorn 接住后走 FastAPI lifespan 收尾 → HarnessPool.close()
逐个关闭 dsh 子进程（SDK close 带 shutdown_timeout 后强杀，不会挂死）。
日志字段（session_id/route/model/usage/耗时）直接写在 message 里。
"""
import logging

import uvicorn

from .app import create_app
from .config import get_settings


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    cfg = get_settings()
    uvicorn.run(create_app(cfg), host=cfg.dsh_serve_host,
                port=cfg.dsh_serve_port, log_level="info")


if __name__ == "__main__":
    main()
