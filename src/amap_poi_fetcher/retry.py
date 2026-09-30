"""指数退避等待时长计算。

等待 = base × 2^attempt + 随机抖动（避免多实例同频重试）。
实际 sleep 由调用方执行，便于测试中注入/断言。
"""

from __future__ import annotations

import random


def backoff_delay(base: float, attempt: int, *, jitter_ratio: float = 0.3) -> float:
    """第 attempt 次重试（从 0 计）的等待秒数。

    参数:
        base: 退避基数（秒）
        attempt: 重试序号，0 表示第一次重试
        jitter_ratio: 抖动幅度比例（0~base*2^attempt*jitter_ratio 的随机量）
    """
    delay = base * (2 ** attempt)
    return delay + random.uniform(0, delay * jitter_ratio)
