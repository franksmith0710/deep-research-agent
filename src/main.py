"""FastAPI 服务入口。"""
import sys
from pathlib import Path

import uvicorn

from src.logging_config import configure_logging, get_logger


def serve() -> None:
    configure_logging()
    logger = get_logger("main")
    logger.info("Server starting on 0.0.0.0:8004")
    sys.path.insert(0, str(Path(__file__).parent.parent))
    uvicorn.run("api.server:app", host="0.0.0.0", port=8004, reload=True)


if __name__ == "__main__":
    serve()
