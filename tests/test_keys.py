"""KeyPool 测试：轮换、失效标记、全灭、状态持久化。"""

import pytest

from amap_poi_fetcher.keys import AllKeysExhausted, KeyPool, KeyState


def test_rotation_skips_unavailable():
    pool = KeyPool(["k1", "k2", "k3"])
    assert pool.current() == "k1"
    pool.mark_dead("k1")
    assert pool.current() == "k2"  # 自动跳过 DEAD
    pool.mark_cooldown("k2")
    assert pool.current() == "k3"


def test_all_exhausted_raises():
    pool = KeyPool(["k1", "k2"])
    pool.mark_dead("k1")
    pool.mark_cooldown("k2")
    with pytest.raises(AllKeysExhausted):
        pool.current()


def test_empty_keys_raises():
    with pytest.raises(ValueError):
        KeyPool([])


def test_state_persistence():
    pool = KeyPool(["k1", "k2", "k3"])
    pool.mark_dead("k2")
    data = pool.to_dict()

    pool2 = KeyPool(["k1", "k2", "k3"])
    pool2.restore(data)
    assert pool2.states["k2"] == KeyState.DEAD
    assert pool2.states["k1"] == KeyState.ACTIVE


def test_mask():
    assert KeyPool.mask("abcdef123456") == "abcde..."
    assert KeyPool.mask("abc") == "***"
