"""配置加载测试：viz.poi_colors 解析与校验、分类粒度判定。"""

import yaml

import pytest

from amap_poi_fetcher.config import ConfigError, load_config, types_granularity


def _write_cfg(tmp_path, body: str) -> str:
    p = tmp_path / "cfg.yaml"
    p.write_text(
        "keys: ['k1']\ntypes: '050301'\nstudy_area: {type: bbox, bounds: [1,2,3,4]}\n" + body,
        encoding="utf-8",
    )
    return str(p)


def test_poi_colors_default(tmp_path):
    cfg = load_config(_write_cfg(tmp_path, ""))
    assert cfg.viz.poi_colors["vec"] == ["#E6194B", "#0055A4", "#3CB44B", "#F58231", "#911EB4"]
    assert cfg.viz.poi_colors["img"] == ["#00FFFF", "#FFFF00", "#00FF00", "#FF00FF", "#FF6600"]


def test_poi_colors_custom(tmp_path):
    cfg = load_config(_write_cfg(tmp_path, yaml.safe_dump({
        "viz": {"poi_colors": {"vec": ["#111111", "#222222"], "img": ["#333333"]}},
    }, allow_unicode=True)))
    assert cfg.viz.poi_colors["vec"] == ["#111111", "#222222"]
    assert cfg.viz.poi_colors["img"] == ["#333333"]


def test_poi_colors_invalid(tmp_path):
    with pytest.raises(ConfigError):
        load_config(_write_cfg(tmp_path, yaml.safe_dump({
            "viz": {"poi_colors": {"vec": [], "img": ["#000000"]}},
        })))
    # 非 # 开头的颜色值视为非法
    bad = tmp_path / "bad.yaml"
    bad.write_text(
        "keys: ['k1']\ntypes: '050301'\nstudy_area: {type: bbox, bounds: [1,2,3,4]}\n"
        "viz:\n  poi_colors:\n    vec: ['red']\n    img: ['#000000']\n",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError):
        load_config(str(bad))


def test_types_granularity():
    assert types_granularity("050301,050302") == "small"
    assert types_granularity("050301") == "small"
    assert types_granularity("050100,050200") == "mid"
    assert types_granularity("0501,0505") == "mid"
    assert types_granularity("050000") == "big"
    assert types_granularity("05") == "big"
    # 混用层级 → 取最粗
    assert types_granularity("050301,050000") == "big"
    assert types_granularity("050301,050100") == "mid"


def test_types_granularity_invalid():
    with pytest.raises(ConfigError):
        types_granularity("")
    with pytest.raises(ConfigError):
        types_granularity("abc")
    with pytest.raises(ConfigError):
        types_granularity("05031")


def test_category_level_in_config(tmp_path):
    p = tmp_path / "cfg.yaml"
    p.write_text(
        "keys: ['k1']\ntypes: '050301,050302'\n"
        "study_area: {type: bbox, bounds: [1,2,3,4]}\n",
        encoding="utf-8",
    )
    assert load_config(str(p)).category_level == "small"
