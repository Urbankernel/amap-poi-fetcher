"""高德 Web 服务 API 封装。

职责：
- /v3/config/district  行政区边界（admin 范围模式用）
- /v3/place/polygon    多边形搜索（count 探测复用第 1 页响应，省一次请求）
- 多 key 轮换（KeyPool）+ QPS 限流/网络异常指数退避重试

错误分类与动作：
- INVALID_USER_KEY 等      → 标记 DEAD，换 key（不计重试次数）
- DAILY_QUERY_OVER_LIMIT   → 标记 COOLDOWN，换 key（不计重试次数）
- CUQPS_HAS_EXCEEDED_THE_LIMIT → 不换 key，指数退避重试，耗尽抛 AmapApiError
- 网络异常 / JSON 解析失败  → 指数退避重试，耗尽抛 AmapApiError
- 其他 status=0            → 不重试，直接抛 AmapApiError
"""

from __future__ import annotations

import logging
import math
import time
from typing import Any, Iterator

import requests

from .keys import AllKeysExhausted, KeyPool
from .retry import backoff_delay

logger = logging.getLogger("amap_poi_fetcher.client")

DISTRICT_URL = "https://restapi.amap.com/v3/config/district"
POLYGON_URL = "https://restapi.amap.com/v3/place/polygon"

KEY_DEAD_INFO = {"INVALID_USER_KEY", "USERKEY_PLAT_NOMATCH", "USER_KEY_RECYCLED"}
KEY_OVER_LIMIT_INFO = {"DAILY_QUERY_OVER_LIMIT"}
QPS_INFO = {"CUQPS_HAS_EXCEEDED_THE_LIMIT"}


class AmapApiError(RuntimeError):
    """非 key 类、非限流类 API 错误，或限流/网络重试耗尽（调度层记失败网格后继续）。"""

    def __init__(self, info: str, infocode: str | None = None):
        self.info = info
        self.infocode = infocode
        super().__init__(f"高德 API 错误: {info}" + (f" (infocode={infocode})" if infocode else ""))


class AmapClient:
    """高德 API 客户端（单线程，内建限速）。

    参数:
        key_pool: 多 key 池
        qps: 每秒请求数上限
        max_retries: 单请求限流/网络异常最大重试次数
        retry_base: 指数退避基数（秒）
        timeout: 单次请求超时（秒）
        sleep_between_pages: 分页间隔（秒）
        session: 可注入自定义 requests.Session（测试用）
    """

    def __init__(
        self,
        key_pool: KeyPool,
        *,
        qps: float = 2.0,
        max_retries: int = 5,
        retry_base: float = 1.0,
        timeout: float = 10.0,
        sleep_between_pages: float = 1.0,
        session: requests.Session | None = None,
    ):
        self.pool = key_pool
        self.qps = qps
        self.max_retries = max_retries
        self.retry_base = retry_base
        self.timeout = timeout
        self.sleep_between_pages = sleep_between_pages
        self.session = session or requests.Session()
        self.request_count = 0  # 实际发出的 HTTP 请求数（含重试）
        self._last_request_ts = 0.0

    # ------------------------------------------------------------------ 内部

    def _throttle(self) -> None:
        """简单限速：保证相邻请求间隔 ≥ 1/qps。"""
        if self.qps <= 0:
            return
        interval = 1.0 / self.qps
        wait = interval - (time.monotonic() - self._last_request_ts)
        if wait > 0:
            time.sleep(wait)
        self._last_request_ts = time.monotonic()

    def _get_json(self, url: str, params: dict[str, Any]) -> dict[str, Any]:
        """带 key 轮换 + 指数退避的 GET，成功返回 JSON dict。

        可能抛出: AllKeysExhausted（全部 key 不可用，调度层需优雅退出）、
        AmapApiError（其他 API 错误或重试耗尽）。
        """
        attempt = 0
        while True:
            key = self.pool.current()  # 可能抛 AllKeysExhausted
            try:
                self._throttle()
                resp = self.session.get(url, params={**params, "key": key}, timeout=self.timeout)
                self.request_count += 1
                data = resp.json()
            except (requests.RequestException, ValueError) as e:
                # 网络异常 / 响应非 JSON → 退避重试
                if attempt >= self.max_retries:
                    raise AmapApiError(f"网络异常重试 {self.max_retries} 次后仍失败: {e}") from e
                delay = backoff_delay(self.retry_base, attempt)
                logger.warning("请求异常（%s），%.1fs 后第 %d/%d 次重试",
                               e, delay, attempt + 1, self.max_retries)
                time.sleep(delay)
                attempt += 1
                continue

            if str(data.get("status")) == "1":
                return data

            info = str(data.get("info", ""))
            infocode = data.get("infocode")
            if info in KEY_DEAD_INFO:
                self.pool.mark_dead(key)
                logger.error("key %s 无效（%s），已弃用并切换", KeyPool.mask(key), info)
                continue  # 换 key，不计重试次数
            if info in KEY_OVER_LIMIT_INFO:
                self.pool.mark_cooldown(key)
                logger.warning("key %s 配额耗尽（%s），已切换", KeyPool.mask(key), info)
                continue
            if info in QPS_INFO:
                if attempt >= self.max_retries:
                    raise AmapApiError(f"QPS 限流重试 {self.max_retries} 次后仍失败", infocode)
                delay = backoff_delay(self.retry_base, attempt)
                logger.warning("QPS 限流，%.1fs 后第 %d/%d 次重试", delay, attempt + 1, self.max_retries)
                time.sleep(delay)
                attempt += 1
                continue
            raise AmapApiError(info, infocode)

    @staticmethod
    def _normalize_types(types: str) -> str:
        """多类型分隔符统一为高德要求的 "|"（配置里允许用逗号，更符合直觉）。"""
        return "|".join(t.strip() for t in types.replace("|", ",").split(",") if t.strip())

    # ------------------------------------------------------------------ 行政区

    def district(self, keywords: str) -> dict[str, str]:
        """查询行政区，返回 {polyline, adcode, name}。polyline 为 GCJ-02。

        keywords 支持 adcode 或行政区名称（名称解析 adcode 也走这里）。
        """
        data = self._get_json(DISTRICT_URL, {
            "keywords": keywords,
            "extensions": "all",
            "subdistrict": 0,
        })
        districts = data.get("districts") or []
        if not districts:
            raise AmapApiError(f"未找到行政区: {keywords}")
        top = districts[0]
        polyline = top.get("polyline") or ""
        if not polyline:
            raise AmapApiError(f"行政区 {keywords}（{top.get('name')}）无边界数据，可能层级过细")
        return {
            "polyline": polyline,
            "adcode": str(top.get("adcode", "")),
            "name": str(top.get("name", "")),
        }

    # ------------------------------------------------------------------ 多边形搜索

    def polygon_first_page(
        self,
        polygon: str,
        types: str,
        *,
        offset: int = 20,
        extensions: str = "all",
    ) -> tuple[int, list[dict[str, Any]]]:
        """请求第 1 页，同时拿到 count（探测与首页数据一次请求完成）。

        返回: (count, 第 1 页 POI 列表)
        """
        data = self._get_json(POLYGON_URL, {
            "types": self._normalize_types(types),
            "polygon": polygon,
            "offset": offset,
            "page": 1,
            "extensions": extensions,
        })
        try:
            count = int(data.get("count", 0))
        except (TypeError, ValueError):
            count = 0
        return count, list(data.get("pois") or [])

    def polygon_iter(
        self,
        polygon: str,
        types: str,
        *,
        offset: int = 20,
        extensions: str = "all",
        count: int,
        first_pois: list[dict[str, Any]],
    ) -> Iterator[dict[str, Any]]:
        """分页迭代 POI：先吐出第 1 页（复用探测响应），再续翻到末尾。

        终止条件：翻到总页数（ceil(count/offset)，修正原脚本 count//20+1 多空页问题）
        或某页返回空（兜底，防高德 count 与实际不一致）。
        """
        yield from first_pois
        total_pages = math.ceil(count / offset) if offset > 0 else 0
        for page in range(2, total_pages + 1):
            if self.sleep_between_pages > 0:
                time.sleep(self.sleep_between_pages)
            data = self._get_json(POLYGON_URL, {
                "types": self._normalize_types(types),
                "polygon": polygon,
                "offset": offset,
                "page": page,
                "extensions": extensions,
            })
            pois = list(data.get("pois") or [])
            if not pois:
                logger.debug("第 %d 页返回空，提前终止翻页", page)
                break
            yield from pois
