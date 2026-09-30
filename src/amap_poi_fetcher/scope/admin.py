"""admin 模式：按行政区（adcode 或名称）获取边界。

边界走高德 /v3/config/district（extensions=all）返回的 polyline（GCJ-02），
解析多地块（"|" 分隔）为 Polygon/MultiPolygon 后逐顶点转 WGS-84。
polyline 解析逻辑移植自原 notebook 的 CityCoordinateExtractor。
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from shapely.geometry import MultiPolygon, Polygon

from ..coords import gcj02_to_wgs84
from . import ScopeResult

if TYPE_CHECKING:
    from ..client import AmapClient

logger = logging.getLogger("amap_poi_fetcher.scope.admin")


def polyline_to_geometry(polyline: str) -> Polygon | MultiPolygon:
    """高德 district polyline（GCJ-02）→ WGS-84 Polygon/MultiPolygon。

    格式：多地块用 "|" 分隔；地块内坐标对用 ";" 分隔；坐标对为 "lng,lat"。
    """
    polygons: list[Polygon] = []
    for block in polyline.split("|"):
        coords: list[tuple[float, float]] = []
        for pair in block.split(";"):
            pair = pair.strip()
            if not pair:
                continue
            try:
                lng, lat = (float(v) for v in pair.split(","))
            except ValueError:
                logger.warning("坐标解析失败，跳过该点: %s", pair)
                continue
            coords.append(gcj02_to_wgs84(lng, lat))
        if len(coords) >= 3:
            poly = Polygon(coords)
            if not poly.is_valid:
                poly = poly.buffer(0)  # 修复自相交
            if not poly.is_empty:
                polygons.append(poly)
    if not polygons:
        raise ValueError("未能从 polyline 解析出有效边界")
    return polygons[0] if len(polygons) == 1 else MultiPolygon(polygons)


def build(cfg: dict[str, Any], client: "AmapClient | None") -> ScopeResult:
    if client is None:
        raise ValueError("admin 模式需要高德客户端（拉取行政区边界）")
    keywords = str(cfg.get("adcode") or cfg.get("name") or "").strip()
    if not keywords:
        raise ValueError("admin 模式需要 adcode（6 位行政区编码）或 name（行政区名称）")
    info = client.district(keywords)
    geom = polyline_to_geometry(info["polyline"])
    name = info["name"] or keywords
    logger.info("行政区边界: %s（adcode=%s）", name, info["adcode"])
    return ScopeResult(geometry=geom, name=name)
