"""高德 POI 编码 → 类别名称 查询（小类/中类/大类三级）。

数据来自 docs/高德POI分类与编码（中英文）_V1.06_20230208.xlsx，
在构建时已解析为 data/poi_types.json：
    {"small": {6位编码: 小类名}, "mid": {4位编码: 中类名}, "big": {2位编码: 大类名}}
运行时零额外依赖（无需 openpyxl）。
"""

from __future__ import annotations

import json
import os

_CACHE: dict[str, dict[str, str]] | None = None

# 编码位数 → 层级
_LEVEL_BY_LEN = {6: "small", 4: "mid", 2: "big"}


def _load() -> dict[str, dict[str, str]]:
    global _CACHE
    if _CACHE is None:
        path = os.path.join(os.path.dirname(__file__), "data", "poi_types.json")
        with open(path, encoding="utf-8") as f:
            _CACHE = json.load(f)
    return _CACHE


def _normalize(code: str | int, level: str) -> str:
    """编码归一到指定层级的位数（6/4/2 位）。"""
    key = str(code).strip().split("|")[0].strip()
    digits = "".join(ch for ch in key if ch.isdigit()) or key
    width = {"small": 6, "mid": 4, "big": 2}[level]
    return digits[:width].zfill(width) if digits.isdigit() else key


def name_of(code: str | int, level: str = "small") -> str:
    """POI 编码 → 指定层级的类别名称；未收录返回空串。

    level: "small"（6 位小类）| "mid"（4 位中类）| "big"（2 位大类）
    """
    key = _normalize(code, level)
    table = _load().get(level, {})
    if key in table:
        return table[key]
    # 兜底：其他层级表里恰好有该编码（如大类的中类行）
    for table_other in _load().values():
        if key in table_other:
            return table_other[key]
    return ""


def label_of(code: str | int, level: str = "small") -> str:
    """图例文字「类别名称（代码）」；未收录时退回纯代码。"""
    key = _normalize(code, level)
    name = name_of(key, level)
    return f"{name}（{key}）" if name else key
