"""结构化日志：控制台 + 文件双通道。

控制台默认 INFO（简洁格式）；run 目录确定后调用 attach_file_handler
追加 DEBUG 级文件通道（run.log，含时间戳，便于排障）。
重复调用安全（不会堆叠 handler）。
"""

from __future__ import annotations

import logging
from pathlib import Path

ROOT_LOGGER = "amap_poi_fetcher"

_CONSOLE_FMT = "%(message)s"
_FILE_FMT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"


def setup_logging(level: int = logging.INFO) -> logging.Logger:
    """初始化包级 logger 与控制台通道，返回包 logger。"""
    logger = logging.getLogger(ROOT_LOGGER)
    logger.setLevel(logging.DEBUG)  # 包级放宽到 DEBUG，由各 handler 自行过滤
    logger.propagate = False

    if not any(getattr(h, "_amap_console", False) for h in logger.handlers):
        console = logging.StreamHandler()
        console.setLevel(level)
        console.setFormatter(logging.Formatter(_CONSOLE_FMT))
        console._amap_console = True  # type: ignore[attr-defined]
        logger.addHandler(console)
    return logger


def attach_file_handler(run_dir: str | Path, level: int = logging.DEBUG) -> Path:
    """为本次运行追加文件日志通道，返回日志文件路径。"""
    logger = logging.getLogger(ROOT_LOGGER)
    log_path = Path(run_dir) / "run.log"
    if not any(getattr(h, "_amap_file", False) for h in logger.handlers):
        file_handler = logging.FileHandler(log_path, encoding="utf-8")
        file_handler.setLevel(level)
        file_handler.setFormatter(logging.Formatter(_FILE_FMT))
        file_handler._amap_file = True  # type: ignore[attr-defined]
        logger.addHandler(file_handler)
    return log_path
