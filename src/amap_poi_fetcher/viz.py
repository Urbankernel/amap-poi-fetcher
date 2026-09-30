"""folium 交互地图：天地图默认底图，导出自包含 HTML。

图层（均可开关）：
- 底图：CartoDB Positron（缺省/备用）| 天地图矢量 | 天地图影像（有 tk 时）
- 叠加：天地图矢量注记 | 天地图影像注记（有 tk 时）
- 研究范围（主色 + 白色衬边光晕）
- 渔网网格（触底超限网格高亮加粗）
- POI 点（按类别着色，popup 显示 name/type/address）

UI 配色随底图联动（注入 JS 监听 Leaflet baselayerchange）：
- 研究范围 #E91E63 + 白衬边光晕、渔网网格 #FF00FF：两主题统一
- POI 按类别（typecode）着色：矢量/Positron（浅色底图）用 poi_colors.vec，
  影像（深色底图）用 poi_colors.img，类别按编码升序依次取色（超出配色数循环复用）
- 疑似截断网格两主题统一 #FF1744（告警色，保持可见性）
- POI 图例「类别名称（编码）」色块随底图联动变色，显示与 POI 图层勾选状态联动

天地图 tk 属个人凭证：从参数或环境变量 TIANDITU_TK 读取，不进 git。
无 tk 时降级 CartoDB Positron 并告警。folium 延迟导入，核心流水线不依赖本模块。
"""

from __future__ import annotations

from collections import Counter

import csv
import json
import logging
import os
from pathlib import Path

from shapely.geometry.base import BaseGeometry

from .grid import Grid
from .poi_types import label_of

logger = logging.getLogger("amap_poi_fetcher.viz")

TIANDITU_TEMPLATES = {
    "vec": ("天地图矢量", "https://t{s}.tianditu.gov.cn/DataServer?T=vec_w&x={x}&y={y}&l={z}&tk={tk}"),
    "cva": ("天地图矢量注记", "https://t{s}.tianditu.gov.cn/DataServer?T=cva_w&x={x}&y={y}&l={z}&tk={tk}"),
    "img": ("天地图影像", "https://t{s}.tianditu.gov.cn/DataServer?T=img_w&x={x}&y={y}&l={z}&tk={tk}"),
    "cia": ("天地图影像注记", "https://t{s}.tianditu.gov.cn/DataServer?T=cia_w&x={x}&y={y}&l={z}&tk={tk}"),
}

# 点数超过该值时提示体积建议（自包含 HTML 体积膨胀，建议抽稀）
POI_COUNT_ADVICE_THRESHOLD = 50000

# 多类型 POI 分类配色默认（配置 viz.poi_colors 可覆盖；vec 浅色底图 / img 深色底图）
DEFAULT_POI_COLORS = {
    "vec": ["#E6194B", "#0055A4", "#3CB44B", "#F58231", "#911EB4"],
    "img": ["#00FFFF", "#FFFF00", "#00FF00", "#FF00FF", "#FF6600"],
}

# 底图联动主题（Leaflet path style 参数；vec 兼作 Positron 浅色底图主题）
THEMES = {
    "vec": {
        "boundary": {"color": "#E91E63", "weight": 2.5, "opacity": 1.0, "fillOpacity": 0.06},
        "casing": {"color": "#FFFFFF", "weight": 5, "opacity": 0.9, "fill": False},
        "grid": {"color": "#FF00FF", "weight": 0.8, "opacity": 0.5, "fill": False},
        "gridTrunc": {"color": "#FF1744", "weight": 2.5, "opacity": 1.0, "fill": False},
    },
    "img": {
        "boundary": {"color": "#E91E63", "weight": 2.5, "opacity": 1.0, "fillOpacity": 0.06},
        "casing": {"color": "#FFFFFF", "weight": 5, "opacity": 0.9, "fill": False},
        "grid": {"color": "#FF00FF", "weight": 1, "opacity": 0.9, "fill": False},
        "gridTrunc": {"color": "#FF1744", "weight": 2.5, "opacity": 1.0, "fill": False},
    },
}

# POI 基础样式（不含 fillColor，fillColor 由类别调色板按 amapCatIndex 决定）
POI_STYLE = {
    "vec": {"color": "#FFFFFF", "weight": 1.5, "opacity": 1.0, "fillOpacity": 0.6},
    "img": {"color": "#FFFFFF", "weight": 1.0, "opacity": 1.0, "fillOpacity": 0.8},
}

# 主题切换 + 图例 JS：监听 baselayerchange 联动；图例随 POI 图层勾选显隐。
# 注意：必须用 window load 延迟执行——本脚本与 folium 的地图初始化同处一个
# <script> 块且位置靠前，顶层直接引用地图/图层变量会 ReferenceError 并拖垮整页。
_THEME_JS = """
window.addEventListener('load', function() {
  var THEMES = %(themes_json)s;
  var POI_STYLE = %(poi_style_json)s;
  var POI_PALETTES = %(poi_palettes_json)s;
  var POI_CATEGORIES = %(poi_categories_json)s;
  var POI_LAYER_NAME = %(poi_layer_name)s;
  var casing = %(casing)s, boundary = %(boundary)s;
  var gridGroup = %(grid_group)s, poiGroup = %(poi_group)s;
  var legend = null;
  var currentTheme = 'vec';
  var catVisible = new Array(POI_CATEGORIES.length).fill(true);
  var allPoiMarkers = [];

  function poiFill(theme, idx) {
    var pal = POI_PALETTES[theme];
    return pal[idx %% pal.length];
  }

  function applyTheme(name) {
    currentTheme = name;
    var t = THEMES[name];
    casing.setStyle(t.casing);
    boundary.setStyle(t.boundary);
    gridGroup.eachLayer(function(l) {
      if (!l.setStyle) return;
      l.setStyle(l.options && l.options.amapTruncated ? t.gridTrunc : t.grid);
    });
    var ps = POI_STYLE[name];
    poiGroup.eachLayer(function(l) {
      if (!l.setStyle) return;
      var i = (l.options && l.options.amapCatIndex) || 0;
      l.setStyle({color: ps.color, weight: ps.weight, opacity: ps.opacity,
                  fillColor: poiFill(name, i), fillOpacity: ps.fillOpacity});
    });
    if (legend) {
      for (var k = 0; k < POI_CATEGORIES.length; k++) {
        var sw = document.getElementById('poi-legend-swatch-' + k);
        if (sw) sw.style.background = poiFill(name, k);
      }
    }
  }

  function refreshVisibility() {
    // 遍历完整点快照，而非 poiGroup 自身——被 removeLayer 的点不在组内，
    // 若遍历 poiGroup 会漏掉它们，导致取消勾选后无法重新显示。
    for (var n = 0; n < allPoiMarkers.length; n++) {
      var l = allPoiMarkers[n];
      if (!l.options) continue;
      var i = l.options.amapCatIndex || 0;
      if (catVisible[i]) {
        if (!poiGroup.hasLayer(l)) {
          var ps = POI_STYLE[currentTheme];
          l.setStyle({color: ps.color, weight: ps.weight, opacity: ps.opacity,
                      fillColor: poiFill(currentTheme, i), fillOpacity: ps.fillOpacity});
          poiGroup.addLayer(l);
        }
      } else {
        if (poiGroup.hasLayer(l)) poiGroup.removeLayer(l);
      }
    }
  }

  function buildLegend() {
    if (!POI_CATEGORIES.length) return;
    legend = document.createElement('div');
    legend.id = 'poi-legend';
    legend.style.cssText = 'position:absolute;bottom:24px;left:12px;z-index:9999;'
      + 'background:rgba(255,255,255,0.92);padding:8px 10px;border-radius:6px;'
      + 'box-shadow:0 1px 4px rgba(0,0,0,0.3);font-size:12px;line-height:1.7;'
      + 'font-family:"Microsoft YaHei",sans-serif;color:#333;';
    var title = document.createElement('div');
    title.textContent = 'POI 图例';
    title.style.cssText = 'font-weight:bold;margin-bottom:2px;';
    legend.appendChild(title);
    for (var k = 0; k < POI_CATEGORIES.length; k++) {
      (function(k) {
        var item = document.createElement('label');
        item.style.cssText = 'display:flex;align-items:center;cursor:pointer;';
        var cb = document.createElement('input');
        cb.type = 'checkbox'; cb.checked = true;
        cb.style.cssText = 'margin:0 4px 0 0;vertical-align:middle;';
        cb.addEventListener('change', function() {
          catVisible[k] = cb.checked;
          refreshVisibility();
        });
        var sw = document.createElement('span');
        sw.id = 'poi-legend-swatch-' + k;
        sw.style.cssText = 'display:inline-block;width:10px;height:10px;border-radius:2px;'
          + 'background:' + poiFill('vec', k) + ';margin-right:4px;';
        item.appendChild(cb);
        item.appendChild(sw);
        item.appendChild(document.createTextNode(
          POI_CATEGORIES[k].label + ' · ' + POI_CATEGORIES[k].count));
        legend.appendChild(item);
      })(k);
    }
    %(map)s.getContainer().appendChild(legend);
  }

  buildLegend();

  // 快照全部 POI 点（初始全显示，eachLayer 能取全；
  // 显隐据此快照增删 poiGroup，避免被移除的点丢失引用）
  poiGroup.eachLayer(function(l) {
    if (l.options && l.options.amapCatIndex !== undefined) allPoiMarkers.push(l);
  });

  %(map)s.on('baselayerchange', function(e) {
    applyTheme(e.name && e.name.indexOf('影像') !== -1 ? 'img' : 'vec');
  });
  %(map)s.on('overlayadd', function(e) {
    if (e.name === POI_LAYER_NAME && legend) legend.style.display = '';
  });
  %(map)s.on('overlayremove', function(e) {
    if (e.name === POI_LAYER_NAME && legend) legend.style.display = 'none';
  });
});
"""


def tianditu_tile_urls(tk: str, layer: str = "vec") -> list[tuple[str, str]]:
    """天地图 XYZ 瓦片地址。layer: vec（矢量+矢量注记）| img（影像+影像注记）。"""
    keys = ("vec", "cva") if layer == "vec" else ("img", "cia")
    return [
        (TIANDITU_TEMPLATES[k][0],
         TIANDITU_TEMPLATES[k][1].format(tk=tk, s="{s}", x="{x}", y="{y}", z="{z}"))
        for k in keys
    ]


def _category_palettes(poi_colors: dict | None, categories: list[str]) -> dict[str, list[str]]:
    """按类别数量生成两套调色板（vec/img），类别数超出配色数时循环复用。"""
    poi_colors = poi_colors or DEFAULT_POI_COLORS
    n = len(categories)
    palettes = {}
    for theme in ("vec", "img"):
        colors = poi_colors.get(theme) or DEFAULT_POI_COLORS[theme]
        palettes[theme] = [colors[i % len(colors)] for i in range(n)]
    return palettes


def _code_at_level(code: str, level: str) -> str:
    """候选编码归一到配置粒度（取前 2/4/6 位数字）。"""
    digits = "".join(ch for ch in code if ch.isdigit())
    width = {"big": 2, "mid": 4, "small": 6}[level]
    return digits[:width] if digits else ""


def _poi_category(row: dict, level: str, configured: set[str]) -> str:
    """单条 POI → 类别编码（已归一到配置粒度）。

    高德部分 POI 的 typecode 为多编码竖线拼接（如 "050301|050200"），
    需拆分后逐个归一：优先取命中用户已配置类别的候选，否则取首个候选
    （即"最终获取的类别"，图例仍显示其规范名称）。
    """
    raw = (row.get("poi_type_code") or "").strip()
    cands = [_code_at_level(c, level) for c in raw.split("|")] if raw else []
    cands = [c for c in cands if c]
    for c in cands:
        if c in configured:
            return c
    return cands[0] if cands else "未知"


def build_map(
    *,
    geometry: BaseGeometry,
    grids: list[Grid],
    truncated_ids: set[str],
    csv_path: str | Path,
    out_path: str | Path,
    basemap: str = "tianditu",
    tk: str | None = None,
    carto_key: str | None = None,
    poi_colors: dict | None = None,
    category_level: str = "small",
    configured_types: str | None = None,
) -> Path:
    """生成自包含交互地图 HTML，返回输出路径。"""
    import folium
    from branca.element import Element
    from shapely.geometry import mapping

    out_path = Path(out_path)
    tk = tk if tk is not None else os.environ.get("TIANDITU_TK", "")
    carto_key = carto_key if carto_key is not None else os.environ.get("CARTO_KEY", "")
    vec = THEMES["vec"]  # 初始主题（矢量/Positron 浅色底图）

    xmin, ymin, xmax, ymax = geometry.bounds
    m = folium.Map(location=[(ymin + ymax) / 2, (xmin + xmax) / 2],
                   zoom_start=10, tiles=None)

    # ---- 底图（与 Baidu_Isochrone 保持一致）----
    # 缺省/备用底图为 CartoDB Positron：OSM 官方瓦片对批量访问有 403 封禁策略，不做缺省。
    # CartoDB 底图自 2023 起要求 API key（无 key 瓦片带 "API key required" 水印），免费申请。
    # 有 tk 时提供天地图矢量/影像两个底图选项 + 矢量注记/影像注记两个叠加层，均可开关。
    tianditu_ok = basemap == "tianditu" and bool(tk)
    if basemap == "tianditu" and not tk:
        logger.warning("未提供天地图 tk（viz.tianditu_tk 或环境变量 TIANDITU_TK），"
                       "底图降级为 CartoDB Positron")

    carto_url = "https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}.png"
    if carto_key:
        carto_url += f"?key={carto_key}"
    else:
        logger.warning("未提供 CartoDB key（viz.carto_key / key_file 的 carto= 行 / 环境变量 CARTO_KEY），"
                       "CartoDB 底图将带 \"API key required\" 水印；"
                       "可到 https://carto.com/basemaps/apikey/ 免费申请")

    folium.TileLayer(
        tiles=carto_url,
        name="CartoDB Positron",
        attr="© OpenStreetMap contributors © CARTO",
        subdomains="abcd",
        show=not tianditu_ok, overlay=False, control=True,
    ).add_to(m)

    if tianditu_ok:
        # 天地图子域为 t0-t7，必须显式指定 subdomains（folium 默认 abc 会解析失败）
        vec_url, cva_url = tianditu_tile_urls(tk, layer="vec")
        img_url, cia_url = tianditu_tile_urls(tk, layer="img")
        folium.TileLayer(tiles=vec_url[1], name=vec_url[0], attr="天地图",
                         subdomains="01234567",
                         show=True, overlay=False, control=True).add_to(m)
        folium.TileLayer(tiles=img_url[1], name=img_url[0], attr="天地图",
                         subdomains="01234567",
                         show=False, overlay=False, control=True).add_to(m)
        folium.TileLayer(tiles=cva_url[1], name=cva_url[0], attr="天地图",
                         subdomains="01234567",
                         show=True, overlay=True, control=True).add_to(m)
        folium.TileLayer(tiles=cia_url[1], name=cia_url[0], attr="天地图",
                         subdomains="01234567",
                         show=False, overlay=True, control=True).add_to(m)

    # ---- 研究范围（主色线 + 白色衬边光晕，归为一组开关） ----
    boundary_group = folium.FeatureGroup(name="研究范围", show=True)
    casing = folium.GeoJson(
        mapping(geometry),
        style_function=lambda _: dict(vec["casing"]),
    )
    casing.add_to(boundary_group)
    boundary = folium.GeoJson(
        mapping(geometry),
        style_function=lambda _: dict(vec["boundary"]),
    )
    boundary.add_to(boundary_group)
    boundary_group.add_to(m)

    # ---- 渔网网格（触底超限高亮；amapTruncated 自定义选项供 JS 识别） ----
    grid_group = folium.FeatureGroup(name="渔网网格", show=True)
    for g in grids:
        truncated = g.gid in truncated_ids
        style = dict(vec["gridTrunc"] if truncated else vec["grid"])
        # folium 0.20 会丢弃构造参数里的自定义键，只能构造后补写进 options
        # （否则 JS 端 l.options.amapTruncated 取不到，切底图后截断高亮失效）
        poly = folium.Polygon(
            locations=[(g.ymax, g.xmin), (g.ymax, g.xmax), (g.ymin, g.xmax), (g.ymin, g.xmin)],
            tooltip=f"{g.gid} (depth={g.depth})" + (" ⚠️疑似截断" if truncated else ""),
            **style,
        )
        poly.options["amapTruncated"] = truncated
        poly.add_to(grid_group)
    grid_group.add_to(m)

    # ---- POI 点：按类别着色（粒度 = 用户配置的类型编码层级） ----
    rows: list[dict] = []
    csv_path = Path(csv_path)
    if csv_path.exists():
        with open(csv_path, newline="", encoding="utf-8-sig") as f:
            rows = list(csv.DictReader(f))
    if len(rows) > POI_COUNT_ADVICE_THRESHOLD:
        logger.warning("POI 点数 %d 较多，自包含 HTML 体积可能较大；建议抽稀", len(rows))

    # 用户配置的类别集合（归一到粒度）；POI typecode 可能是多编码竖线拼接，需拆分匹配
    configured: set[str] = set()
    if configured_types:
        configured = {_code_at_level(t.strip(), category_level)
                      for t in configured_types.replace("|", ",").split(",")
                      if t.strip() and _code_at_level(t.strip(), category_level)}

    # 类别 = 每条 POI 归一后的类别编码，编码升序保证图例与配色稳定
    categories: list[str] = []
    seen: set[str] = set()
    row_category: list[str] = []
    for row in rows:
        code = _poi_category(row, category_level, configured)
        row_category.append(code)
        if code not in seen:
            seen.add(code)
            categories.append(code)
    categories.sort()
    cat_index = {code: i for i, code in enumerate(categories)}
    cat_counts = Counter(row_category)
    palettes = _category_palettes(poi_colors, categories)

    # 类别数超过配色数时提示（颜色仍会循环复用，不报错）
    for theme in ("vec", "img"):
        colors = (poi_colors or DEFAULT_POI_COLORS).get(theme) or DEFAULT_POI_COLORS[theme]
        if len(categories) > len(colors):
            logger.warning("POI 类别数 %d 超过 viz.poi_colors.%s 配色数 %d，"
                           "颜色将循环复用；可在配置中增加配色", len(categories), theme, len(colors))
            break

    poi_layer_name = f"POI（{len(rows)}）"
    poi_group = folium.FeatureGroup(name=poi_layer_name)
    for row, code in zip(rows, row_category):
        try:
            lat, lon = float(row["lat"]), float(row["lon"])
        except (KeyError, ValueError):
            continue
        idx = cat_index[code]
        popup_html = (
            f"<b>{row.get('name', '')}</b><br>"
            f"{row.get('poi_type', '')}<br>"
            f"{row.get('address', '')}"
        )
        # folium 0.20 会丢弃构造参数里的自定义键，amapCatIndex 只能构造后补写
        # 进 options（否则 JS 端切底图时取不到类别索引，全部退化为第 0 色）
        marker = folium.CircleMarker(
            location=[lat, lon], radius=3,
            color=POI_STYLE["vec"]["color"], weight=POI_STYLE["vec"]["weight"],
            opacity=POI_STYLE["vec"]["opacity"],
            fill=True, fill_color=palettes["vec"][idx],
            fill_opacity=POI_STYLE["vec"]["fillOpacity"],
            popup=folium.Popup(popup_html, max_width=260),
        )
        marker.options["amapCatIndex"] = idx
        marker.add_to(poi_group)
    poi_group.add_to(m)

    folium.LayerControl(collapsed=False).add_to(m)

    # ---- 底图联动主题切换 + POI 图例 JS ----
    theme_js = _THEME_JS % {
        "themes_json": json.dumps(THEMES, ensure_ascii=False),
        "poi_style_json": json.dumps(POI_STYLE, ensure_ascii=False),
        "poi_palettes_json": json.dumps(palettes, ensure_ascii=False),
        "poi_categories_json": json.dumps(
            [{"label": label_of(c, category_level), "count": cat_counts.get(c, 0)}
             for c in categories], ensure_ascii=False),
        "poi_layer_name": json.dumps(poi_layer_name, ensure_ascii=False),
        "casing": casing.get_name(),
        "boundary": boundary.get_name(),
        "grid_group": grid_group.get_name(),
        "poi_group": poi_group.get_name(),
        "map": m.get_name(),
    }
    m.get_root().script.add_child(Element(theme_js))

    m.save(str(out_path))
    logger.info("交互地图: %s（POI %d 点，网格 %d 个，类别 %d 个）",
                out_path, len(rows), len(grids), len(categories))
    return out_path
