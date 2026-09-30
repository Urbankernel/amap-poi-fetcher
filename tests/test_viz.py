"""viz 多类型 POI 分类着色与图例测试（静态断言生成的 HTML）。"""

import csv
import json

from shapely.geometry import box

from amap_poi_fetcher.grid import Grid
from amap_poi_fetcher.store import FIELDNAMES
from amap_poi_fetcher.viz import build_map


def _write_csv(path, rows):
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=FIELDNAMES)
        w.writeheader()
        for r in rows:
            w.writerow(r)


def _poi(pid, code, lng, lat):
    return {"poi_id": pid, "lon": lng, "lat": lat, "lon_gcj02": lng, "lat_gcj02": lat,
            "name": f"店{pid}", "poi_type": f"x;y;{pid}", "poi_type_code": code,
            "cityname": "上海市", "adname": "浦东新区", "address": "addr", "grid_id": "g0_0"}


def test_multi_category_coloring_and_legend(tmp_path):
    rows = [
        _poi("A", "050301", 121.5, 31.2),   # 肯德基
        _poi("B", "050301", 121.51, 31.21),
        _poi("C", "050302", 121.52, 31.22),  # 麦当劳
    ]
    csv_path = tmp_path / "pois.csv"
    _write_csv(csv_path, rows)

    html = build_map(
        geometry=box(121.4, 31.1, 121.6, 31.3),
        grids=[Grid("g0_0", 121.4, 31.1, 121.6, 31.3)],
        truncated_ids=set(),
        csv_path=csv_path,
        out_path=tmp_path / "map.html",
        basemap="tianditu", tk="fake_tk",
    ).read_text(encoding="utf-8")

    # 图例文字：类别名称（代码）
    assert "肯德基（050301）" in html
    assert "麦当劳（050302）" in html
    assert "POI 图例" in html
    # 分类调色板：vec/img 各取前两色
    assert "#E6194B" in html and "#0055A4" in html        # vec 第1、2类
    assert "#00FFFF" in html and "#FFFF00" in html        # img 第1、2类
    # 每个 marker 带类别索引选项，供 JS 按底图重着色
    assert "amapCatIndex" in html
    # 图例与 POI 图层勾选联动 + 底图切换联动
    assert "overlayadd" in html and "overlayremove" in html
    assert "baselayerchange" in html


def test_category_cycle_beyond_palette():
    # 7 个类别超过 5 色 → 循环复用（第 6、7 类复用第 1、2 色）
    from amap_poi_fetcher.viz import _category_palettes
    palettes = _category_palettes(None, [str(i) for i in range(7)])
    assert palettes["vec"] == ["#E6194B", "#0055A4", "#3CB44B", "#F58231", "#911EB4",
                               "#E6194B", "#0055A4"]
    assert palettes["img"][5] == "#00FFFF" and palettes["img"][6] == "#FFFF00"


def test_single_category_legend(tmp_path):
    rows = [_poi("A", "050301", 121.5, 31.2)]
    csv_path = tmp_path / "pois.csv"
    _write_csv(csv_path, rows)
    html = build_map(
        geometry=box(121.4, 31.1, 121.6, 31.3),
        grids=[Grid("g0_0", 121.4, 31.1, 121.6, 31.3)],
        truncated_ids=set(), csv_path=csv_path,
        out_path=tmp_path / "map.html", basemap="tianditu", tk="fake_tk",
    ).read_text(encoding="utf-8")
    assert "肯德基（050301）" in html
    assert "POI 图例" in html


def test_piped_typecode_not_in_legend(tmp_path):
    """typecode 多编码竖线拼接（如 050301|050200）应拆分匹配，图例不出现竖线垃圾项。"""
    rows = [
        _poi("A", "050301|050200", 121.5, 31.2),   # 命中已配置的 050301
        _poi("B", "052301|070500", 121.51, 31.21),  # 无命中 → 取首个候选 052301
    ]
    csv_path = tmp_path / "pois.csv"
    _write_csv(csv_path, rows)
    html = build_map(
        geometry=box(121.4, 31.1, 121.6, 31.3),
        grids=[Grid("g0_0", 121.4, 31.1, 121.6, 31.3)],
        truncated_ids=set(), csv_path=csv_path,
        out_path=tmp_path / "map.html", basemap="tianditu", tk="fake_tk",
        configured_types="050301,050302",
    ).read_text(encoding="utf-8")
    labels = [c["label"] for c in json.loads(_legend_data(html))]
    assert all("|" not in label for label in labels)   # 图例无竖线拼接项
    assert "肯德基（050301）" in labels                 # 命中已配置类别
    assert "052301" in labels                          # 未收录编码退回纯代码


def _legend_data(html: str) -> str:
    """提取图例数据段（POI_CATEGORIES JSON）。"""
    import re
    return re.search(r'var POI_CATEGORIES = (\[.*?\]);', html).group(1)


def test_mid_level_grouping(tmp_path):
    """配置中类（4 位）时按中类分组：不同小类归入同一中类图例。"""
    rows = [
        _poi("A", "050101|050102", 121.5, 31.2),   # 两个小类都属中餐厅 0501
        _poi("B", "050501", 121.51, 31.21),        # 咖啡厅 0505
    ]
    csv_path = tmp_path / "pois.csv"
    _write_csv(csv_path, rows)
    html = build_map(
        geometry=box(121.4, 31.1, 121.6, 31.3),
        grids=[Grid("g0_0", 121.4, 31.1, 121.6, 31.3)],
        truncated_ids=set(), csv_path=csv_path,
        out_path=tmp_path / "map.html", basemap="tianditu", tk="fake_tk",
        category_level="mid", configured_types="0501,0505",
    ).read_text(encoding="utf-8")
    assert "中餐厅（0501）" in html
    assert "咖啡厅（0505）" in html
    assert "（050101）" not in html and "（050102）" not in html  # 不按小类列出


def test_big_level_grouping(tmp_path):
    """配置大类（2 位）时按大类分组：所有餐饮 POI 归入餐饮服务（05）。"""
    rows = [
        _poi("A", "050301|050200", 121.5, 31.2),
        _poi("B", "050501", 121.51, 31.21),
    ]
    csv_path = tmp_path / "pois.csv"
    _write_csv(csv_path, rows)
    html = build_map(
        geometry=box(121.4, 31.1, 121.6, 31.3),
        grids=[Grid("g0_0", 121.4, 31.1, 121.6, 31.3)],
        truncated_ids=set(), csv_path=csv_path,
        out_path=tmp_path / "map.html", basemap="tianditu", tk="fake_tk",
        category_level="big", configured_types="05",
    ).read_text(encoding="utf-8")
    assert "餐饮服务（05）" in html
    assert "（050301）" not in html and "（050501）" not in html  # 不按小类列出
