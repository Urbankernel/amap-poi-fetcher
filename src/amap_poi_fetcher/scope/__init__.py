"""范围输入四模式：buffer / admin / bbox / file → 统一 WGS-84 几何。

坐标系纪律：外部输入可以是 GCJ-02（高德边界 / 显式声明的 bbox），
进入系统内部一律归一 WGS-84。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from shapely.geometry.base import BaseGeometry

if TYPE_CHECKING:
    from ..client import AmapClient


@dataclass
class ScopeResult:
    """范围解析结果：WGS-84 几何 + 人类可读名称（用于 run 目录标签与地图标注）。"""

    geometry: BaseGeometry
    name: str


def load_scope(cfg: dict[str, Any], client: "AmapClient | None" = None) -> ScopeResult:
    """按 study_area.type 分发到具体模式实现。"""
    scope_type = cfg.get("type")
    if scope_type == "buffer":
        from .buffer import build
        return build(cfg)
    if scope_type == "admin":
        from .admin import build
        return build(cfg, client)
    if scope_type == "bbox":
        from .bbox import build
        return build(cfg)
    if scope_type == "file":
        from .fileio import build
        return build(cfg)
    raise ValueError(f"未知 study_area.type: {scope_type!r}（支持 buffer/admin/bbox/file）")
