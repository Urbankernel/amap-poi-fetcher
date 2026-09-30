"""YAML 配置加载 + CLI 覆盖 + 校验。

key 三个来源（优先级从高到低）：配置 keys 列表 / key_file 文件 / 环境变量 AMAP_KEYS。
CLI 参数（--keys/--types/--adcode/--bbox/--grid-size/--output-dir）覆盖 YAML 同名项。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


class ConfigError(ValueError):
    """配置错误（缺 key、范围参数不全、数值越界等）。"""


@dataclass
class GridConfig:
    size: float = 0.1
    split_threshold: int = 200
    max_depth: int = 4
    min_size: float = 0.005


@dataclass
class RequestConfig:
    offset: int = 20
    qps: float = 2.0
    sleep_between_pages: float = 1.0
    max_retries: int = 5
    retry_base_seconds: float = 1.0
    timeout: float = 10.0


@dataclass
class OutputsConfig:
    dir: str = "runs"
    csv: bool = True
    geojson: bool = True
    html: bool = True
    gpkg: bool = True   # GeoPackage（优先矢量格式，需 geopandas）
    shp: bool = False   # Shapefile（兼容矢量格式，需 geopandas）


@dataclass
class VizConfig:
    basemap: str = "tianditu"
    tianditu_tk: str = ""  # 天地图 tk；留空回退 key_file 的 tk= 行 / 环境变量 TIANDITU_TK，都没有则降级 CartoDB
    carto_key: str = ""  # CartoDB 底图 key；留空则 CartoDB 瓦片带 "API key required" 水印
    # 多类型 POI 按类别着色：vec 为矢量/浅色底图配色，img 为影像/深色底图配色
    # 按类别数量依次取色；类别数超过列表长度时循环复用（不推荐类别过多）
    poi_colors: dict[str, list[str]] = field(default_factory=lambda: {
        "vec": ["#E6194B", "#0055A4", "#3CB44B", "#F58231", "#911EB4"],
        "img": ["#00FFFF", "#FFFF00", "#00FF00", "#FF00FF", "#FF6600"],
    })


@dataclass
class Config:
    keys: list[str]
    types: str
    extensions: str
    study_area: dict[str, Any]
    grid: GridConfig
    request: RequestConfig
    outputs: OutputsConfig
    viz: VizConfig
    filter_by_scope: bool = True  # 入库前按范围几何过滤网格矩形带回来的范围外 POI
    category_level: str = "small"  # 地图图例/配色分类粒度：small(6位)/mid(4位)/big(2位)
    raw: dict[str, Any] = field(default_factory=dict)  # 合并覆盖后的原始配置（快照用）


def types_granularity(types: str) -> str:
    """根据配置的 POI 类型编码判定分类粒度：big（大类）/ mid（中类）/ small（小类）。

    判定规则（按编码尾部零）：
    - 6 位且以 0000 结尾（如 050000）或 2 位编码（如 05）→ 大类
    - 6 位且以 00 结尾（如 050100）或 4 位编码（如 0501）→ 中类
    - 其余 6 位（如 050301）→ 小类
    多个编码层级混用时取最粗粒度（大类 > 中类 > 小类）并告警。
    """
    import logging
    logger = logging.getLogger("amap_poi_fetcher.config")

    codes = [t.strip() for t in types.replace("|", ",").split(",") if t.strip()]
    if not codes:
        raise ConfigError("types 为空，无法判定分类粒度")
    levels: set[str] = set()
    for c in codes:
        if not c.isdigit():
            raise ConfigError(f"POI 类型编码必须是数字: {c!r}")
        if len(c) == 2 or (len(c) == 6 and c.endswith("0000")):
            levels.add("big")
        elif len(c) == 4 or (len(c) == 6 and c.endswith("00")):
            levels.add("mid")
        elif len(c) == 6:
            levels.add("small")
        else:
            raise ConfigError(f"POI 类型编码长度须为 2/4/6 位: {c!r}")
    if len(levels) == 1:
        return levels.pop()
    level = "big" if "big" in levels else "mid"
    logger.warning("types 编码层级混用 %s，分类粒度取最粗: %s", sorted(levels), level)
    return level


def _load_keys(raw: dict[str, Any]) -> list[str]:
    keys = raw.get("keys") or []
    if isinstance(keys, str):
        keys = [keys]
    # 过滤占位符，避免示例配置直接跑时拿 "你的key1" 发请求
    keys = [str(k).strip() for k in keys if str(k).strip() and not str(k).startswith("你的")]

    if not keys and raw.get("key_file"):
        key_file = Path(raw["key_file"]).expanduser()
        if not key_file.exists():
            raise ConfigError(f"key_file 不存在: {key_file}")
        with open(key_file, encoding="utf-8") as f:
            # 无前缀行=高德 key；tk=/carto= 前缀行分别为天地图 tk / CartoDB key（见各自 _load_*），此处跳过
            keys = [line.strip() for line in f
                    if line.strip() and not line.strip().startswith("#")
                    and not line.strip().lower().startswith("tk=")
                    and not line.strip().lower().startswith("carto=")]

    if not keys:
        env = os.environ.get("AMAP_KEYS", "")
        keys = [k.strip() for k in env.split(",") if k.strip()]

    if not keys:
        raise ConfigError(
            "未配置任何高德 key：请在配置文件 keys / key_file 中填写，"
            "或设置环境变量 AMAP_KEYS（多个用逗号分隔）"
        )
    return keys


def _load_tianditu_tk(raw: dict[str, Any]) -> str:
    """天地图 tk 三级来源（优先级从高到低）：配置 viz.tianditu_tk > key_file 的 tk= 行 > 环境变量 TIANDITU_TK。"""
    # 1) 配置文件字段（手动覆盖，优先级最高）
    cfg_tk = str((raw.get("viz") or {}).get("tianditu_tk") or "").strip()
    if cfg_tk:
        return cfg_tk
    # 2) key_file 里的 tk= 前缀行（与高德 key 同文件，统一凭证管理）
    key_file = raw.get("key_file")
    if key_file:
        p = Path(key_file).expanduser()
        if p.exists():
            with open(p, encoding="utf-8") as f:
                for line in f:
                    s = line.strip()
                    if s.lower().startswith("tk="):
                        tk = s.split("=", 1)[1].strip()
                        if tk:
                            return tk
    # 3) 环境变量
    return os.environ.get("TIANDITU_TK", "").strip()


def _load_carto_key(raw: dict[str, Any]) -> str:
    """CartoDB 底图 key 三级来源（优先级从高到低）：配置 viz.carto_key > key_file 的 carto= 行 > 环境变量 CARTO_KEY。"""
    # 1) 配置文件字段（手动覆盖，优先级最高）
    cfg_key = str((raw.get("viz") or {}).get("carto_key") or "").strip()
    if cfg_key:
        return cfg_key
    # 2) key_file 里的 carto= 前缀行（与高德 key / 天地图 tk 同文件，统一凭证管理）
    key_file = raw.get("key_file")
    if key_file:
        p = Path(key_file).expanduser()
        if p.exists():
            with open(p, encoding="utf-8") as f:
                for line in f:
                    s = line.strip()
                    if s.lower().startswith("carto="):
                        k = s.split("=", 1)[1].strip()
                        if k:
                            return k
    # 3) 环境变量
    return os.environ.get("CARTO_KEY", "").strip()


def _validate_study_area(sa: dict[str, Any]) -> None:
    scope_type = sa.get("type")
    if scope_type not in ("buffer", "admin", "bbox", "file"):
        raise ConfigError(f"study_area.type 必须是 buffer/admin/bbox/file 之一，当前: {scope_type!r}")
    if scope_type == "buffer" and not (sa.get("center") and sa.get("radius_km")):
        raise ConfigError("buffer 模式需要 center 与 radius_km")
    if scope_type == "admin" and not (sa.get("adcode") or sa.get("name")):
        raise ConfigError("admin 模式需要 adcode 或 name")
    if scope_type == "bbox" and not sa.get("bounds"):
        raise ConfigError("bbox 模式需要 bounds: [xmin, ymin, xmax, ymax]")
    if scope_type == "file" and not sa.get("path"):
        raise ConfigError("file 模式需要 path")


def load_config(path: str | Path, overrides: dict[str, Any] | None = None) -> Config:
    """加载 YAML 配置并应用 CLI 覆盖，返回校验后的 Config。"""
    path = Path(path)
    if not path.exists():
        raise ConfigError(f"配置文件不存在: {path}")
    with open(path, encoding="utf-8") as f:
        raw: dict[str, Any] = yaml.safe_load(f) or {}

    # ---- CLI 覆盖 ----
    ov = overrides or {}
    if ov.get("keys"):
        raw["keys"] = [k.strip() for k in ov["keys"].split(",") if k.strip()]
    if ov.get("types"):
        raw["types"] = ov["types"]
    if ov.get("adcode"):
        raw["study_area"] = {"type": "admin", "adcode": ov["adcode"]}
    if ov.get("bbox"):
        raw["study_area"] = {"type": "bbox", "bounds": ov["bbox"]}
    if ov.get("grid_size"):
        raw.setdefault("grid", {})["size"] = ov["grid_size"]
    if ov.get("output_dir"):
        raw.setdefault("outputs", {})["dir"] = ov["output_dir"]

    # ---- 组装与校验 ----
    keys = _load_keys(raw)
    types = str(raw.get("types") or "").strip()
    if not types:
        raise ConfigError("未配置 POI 类型编码 types（参考高德 POI 分类编码表，建议小类）")

    study_area = raw.get("study_area") or {}
    _validate_study_area(study_area)

    g = raw.get("grid") or {}
    grid = GridConfig(
        size=float(g.get("size", 0.1)),
        split_threshold=int(g.get("split_threshold", 200)),
        max_depth=int(g.get("max_depth", 4)),
        min_size=float(g.get("min_size", 0.005)),
    )
    if grid.size <= 0 or grid.min_size <= 0 or grid.min_size >= grid.size:
        raise ConfigError(f"grid.size/min_size 不合法: size={grid.size}, min_size={grid.min_size}")
    if grid.split_threshold <= 0:
        raise ConfigError("grid.split_threshold 必须为正整数")

    r = raw.get("request") or {}
    request = RequestConfig(
        offset=int(r.get("offset", 20)),
        qps=float(r.get("qps", 2.0)),
        sleep_between_pages=float(r.get("sleep_between_pages", 1.0)),
        max_retries=int(r.get("max_retries", 5)),
        retry_base_seconds=float(r.get("retry_base_seconds", 1.0)),
        timeout=float(r.get("timeout", 10.0)),
    )
    if not (1 <= request.offset <= 25):
        raise ConfigError(f"request.offset 必须在 1~25 之间（高德上限 25），当前: {request.offset}")

    o = raw.get("outputs") or {}
    outputs = OutputsConfig(
        dir=str(o.get("dir", "runs")),
        csv=bool(o.get("csv", True)),
        geojson=bool(o.get("geojson", True)),
        html=bool(o.get("html", True)),
        gpkg=bool(o.get("gpkg", True)),
        shp=bool(o.get("shp", False)),
    )

    v = raw.get("viz") or {}
    _default_poi_colors = {
        "vec": ["#E6194B", "#0055A4", "#3CB44B", "#F58231", "#911EB4"],
        "img": ["#00FFFF", "#FFFF00", "#00FF00", "#FF00FF", "#FF6600"],
    }
    poi_colors_cfg = v.get("poi_colors") or {}
    poi_colors = {
        "vec": poi_colors_cfg.get("vec") if poi_colors_cfg.get("vec") is not None
               else _default_poi_colors["vec"],
        "img": poi_colors_cfg.get("img") if poi_colors_cfg.get("img") is not None
               else _default_poi_colors["img"],
    }
    for theme in ("vec", "img"):
        if not poi_colors[theme] or not all(str(c).startswith("#") for c in poi_colors[theme]):
            raise ConfigError(f"viz.poi_colors.{theme} 必须是非空的颜色列表（如 #E6194B）")
    viz = VizConfig(
        basemap=str(v.get("basemap", "tianditu")),
        tianditu_tk=_load_tianditu_tk(raw),
        carto_key=_load_carto_key(raw),
        poi_colors=poi_colors,
    )
    if viz.basemap not in ("tianditu", "cartodb"):
        raise ConfigError(f"viz.basemap 仅支持 tianditu/cartodb，当前: {viz.basemap!r}")

    extensions = str(raw.get("extensions", "all"))
    if extensions not in ("all", "base"):
        raise ConfigError(f"extensions 仅支持 all/base，当前: {extensions!r}")

    return Config(
        keys=keys, types=types, extensions=extensions,
        study_area=study_area, grid=grid, request=request,
        outputs=outputs, viz=viz,
        filter_by_scope=bool(raw.get("filter_by_scope", True)),
        category_level=types_granularity(types),
        raw=raw,
    )


def type_count(types: str) -> int:
    """类型编码个数（逗号或 | 分隔）。"""
    return len([t for t in types.replace("|", ",").split(",") if t.strip()])
