"""AmapClient 测试（假 session，不发真实请求）。

覆盖脏数据场景：key 失效轮换、配额耗尽、QPS 退避、网络连续超时、
多类型分隔符归一、分页空页兜底。
"""

import pytest
import requests

from amap_poi_fetcher.client import AmapApiError, AmapClient
from amap_poi_fetcher.keys import AllKeysExhausted, KeyPool, KeyState


class FakeResponse:
    def __init__(self, data):
        self._data = data

    def json(self):
        return self._data


class FakeSession:
    """按脚本返回响应或抛异常的假 session。script 每项为 dict 或 Exception。"""

    def __init__(self, script):
        self.script = list(script)
        self.calls: list[dict] = []

    def get(self, url, params=None, timeout=None):
        self.calls.append({"url": url, "params": dict(params or {})})
        if not self.script:
            raise AssertionError("FakeSession 脚本耗尽，收到了计划外请求")
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return FakeResponse(item)


def ok(count="1", pois=None):
    return {"status": "1", "count": str(count),
            "pois": pois if pois is not None else [{"id": "P1"}]}


def make_client(script, keys=("k1", "k2")):
    session = FakeSession(script)
    client = AmapClient(
        KeyPool(list(keys)), qps=10000, max_retries=3, retry_base=0.001,
        timeout=1, sleep_between_pages=0, session=session,
    )
    return client, session


def test_key_rotation_on_invalid():
    client, session = make_client([
        {"status": "0", "info": "INVALID_USER_KEY"},
        ok(),
    ])
    data = client._get_json("http://x", {})
    assert data["status"] == "1"
    assert session.calls[0]["params"]["key"] == "k1"
    assert session.calls[1]["params"]["key"] == "k2"
    assert client.pool.states["k1"] == KeyState.DEAD


def test_all_keys_exhausted():
    client, _ = make_client([
        {"status": "0", "info": "INVALID_USER_KEY"},
        {"status": "0", "info": "DAILY_QUERY_OVER_LIMIT"},
    ])
    with pytest.raises(AllKeysExhausted):
        client._get_json("http://x", {})


def test_qps_retry_then_success():
    client, session = make_client([
        {"status": "0", "info": "CUQPS_HAS_EXCEEDED_THE_LIMIT"},
        ok(),
    ])
    data = client._get_json("http://x", {})
    assert data["status"] == "1"
    assert len(session.calls) == 2  # 同一 key 重试，不轮换
    assert all(c["params"]["key"] == "k1" for c in session.calls)


def test_network_retry_exhausted():
    client, _ = make_client([requests.ConnectionError("boom")] * 4)
    with pytest.raises(AmapApiError, match="网络异常"):
        client._get_json("http://x", {})


def test_other_api_error_no_retry():
    client, session = make_client([{"status": "0", "info": "INVALID_PARAMS", "infocode": "20001"}])
    with pytest.raises(AmapApiError, match="INVALID_PARAMS"):
        client._get_json("http://x", {})
    assert len(session.calls) == 1  # 不重试


def test_types_separator_normalized():
    client, session = make_client([ok(count="0", pois=[])])
    client.polygon_first_page("1,2|3,4", "050301,050302")
    assert session.calls[0]["params"]["types"] == "050301|050302"


def test_pagination_stops_on_empty_page():
    # count=41 → 3 页；第 3 页返回空 → 提前终止
    client, _ = make_client([
        ok(count="41", pois=[{"id": f"P{i}"} for i in range(20)]),
        ok(count="41", pois=[{"id": f"P{i}"} for i in range(20, 40)]),
        ok(count="41", pois=[]),
    ])
    count, first = client.polygon_first_page("1,2|3,4", "050301")
    pois = list(client.polygon_iter("1,2|3,4", "050301", count=count, first_pois=first))
    assert len(pois) == 40


def test_pagination_exact_multiple_no_extra_page():
    # count=40 整除 → 恰好 2 页（修正原脚本 count//20+1 多空页 bug）
    client, session = make_client([
        ok(count="40", pois=[{"id": f"P{i}"} for i in range(20)]),
        ok(count="40", pois=[{"id": f"P{i}"} for i in range(20, 40)]),
    ])
    count, first = client.polygon_first_page("1,2|3,4", "050301")
    pois = list(client.polygon_iter("1,2|3,4", "050301", count=count, first_pois=first))
    assert len(pois) == 40
    assert len(session.calls) == 2
