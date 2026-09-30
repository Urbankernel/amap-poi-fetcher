"""退避序列测试：指数增长、含非负抖动。"""

from amap_poi_fetcher.retry import backoff_delay


def test_exponential_growth():
    d0 = backoff_delay(1.0, 0, jitter_ratio=0)
    d1 = backoff_delay(1.0, 1, jitter_ratio=0)
    d2 = backoff_delay(1.0, 2, jitter_ratio=0)
    assert (d0, d1, d2) == (1.0, 2.0, 4.0)


def test_jitter_nonnegative():
    for _ in range(50):
        assert backoff_delay(1.0, 0) >= 1.0
