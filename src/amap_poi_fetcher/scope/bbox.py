"""bbox 模式：经纬度矩形 [xmin, ymin, xmax, ymax]。

coord_type 声明输入坐标系（wgs84/gcj02），GCJ-02 自动转 WGS-84。
"""

from __future__ import annotations

from typing import Any

from shapely.geometry import box

from ..coords import gcj02_to_wgs84
from . import ScopeResult


def build(cfg: dict[str, Any]) -> ScopeResult:
    bounds = cfg.get("bounds")
    if not bounds or len(bounds) != 4:
        raise ValueError("bbox 模式需要 bounds: [xmin, ymin, xmax, ymax]")
    xmin, ymin, xmax, ymax = (float(v) for v in bounds)
    if not (xmin < xmax and ymin < ymax):
        raise ValueError(f"bbox 坐标不合法（要求 xmin<xmax 且 ymin<ymax）: {bounds}")

    coord_type = str(cfg.get("coord_type", "wgs84")).lower()
    if coord_type == "gcj02":
        xmin, ymin = gcj02_to_wgs84(xmin, ymin)
        xmax, ymax = gcj02_to_wgs84(xmax, ymax)
    elif coord_type != "wgs84":
        raise ValueError(f"未知 coord_type: {coord_type!r}（支持 wgs84/gcj02）")

    return ScopeResult(geometry=box(xmin, ymin, xmax, ymax), name="bbox")
