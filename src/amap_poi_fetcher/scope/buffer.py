"""buffer 模式：以某点为中心的圆形缓冲。

coord_type 声明 center 的输入坐标系（wgs84/gcj02），GCJ-02 自动转 WGS-84。
用局部等距圆柱近似生成圆多边形（1°纬 ≈ 110.94km，1°经 ≈ 111.32·cos(lat)km），
对 POI 抓取场景的公里级缓冲精度足够，不引入 proj 重依赖。
"""

from __future__ import annotations

import math
from typing import Any

from shapely.geometry import Polygon

from ..coords import gcj02_to_wgs84
from . import ScopeResult

_KM_PER_DEG_LAT = 110.94
_KM_PER_DEG_LON = 111.32


def circle_polygon(center: list[float], radius_km: float, vertices: int = 64) -> Polygon:
    """生成近似圆多边形（WGS-84）。"""
    lon, lat = float(center[0]), float(center[1])
    dx = radius_km / (_KM_PER_DEG_LON * math.cos(math.radians(lat)))
    dy = radius_km / _KM_PER_DEG_LAT
    points = [
        (lon + dx * math.cos(2 * math.pi * i / vertices),
         lat + dy * math.sin(2 * math.pi * i / vertices))
        for i in range(vertices)
    ]
    return Polygon(points)


def build(cfg: dict[str, Any]) -> ScopeResult:
    center = cfg.get("center")
    radius_km = cfg.get("radius_km")
    if not center or len(center) != 2:
        raise ValueError("buffer 模式需要 center: [lon, lat]")
    if not radius_km or float(radius_km) <= 0:
        raise ValueError("buffer 模式需要正的 radius_km")

    # coord_type 声明 center 的输入坐标系（wgs84/gcj02），GCJ-02 自动转 WGS-84
    coord_type = str(cfg.get("coord_type", "wgs84")).lower()
    if coord_type == "gcj02":
        center = list(gcj02_to_wgs84(float(center[0]), float(center[1])))
    elif coord_type != "wgs84":
        raise ValueError(f"未知 coord_type: {coord_type!r}（支持 wgs84/gcj02）")

    vertices = int(cfg.get("vertices", 64))
    geom = circle_polygon(center, float(radius_km), vertices)
    return ScopeResult(geometry=geom, name=f"buffer_{radius_km}km")
