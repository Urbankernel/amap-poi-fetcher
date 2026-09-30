"""POI 编码 → 名称映射测试。"""

from amap_poi_fetcher.poi_types import label_of, name_of


def test_name_of_known():
    assert name_of("050301") == "肯德基"
    assert name_of("010101") == "中国石化"
    assert name_of("100000") == "住宿服务相关"


def test_name_of_padding():
    # 数值型/缺前导零的编码也能命中
    assert name_of(50301) == "肯德基"


def test_name_of_unknown():
    assert name_of("999999") == ""
    assert name_of("") == ""


def test_label_of():
    assert label_of("050301") == "肯德基（050301）"
    assert label_of(50301) == "肯德基（050301）"
    assert label_of("999999") == "999999"  # 未收录退回纯代码


def test_level_names():
    # 三级映射：小类 / 中类 / 大类
    assert name_of("0501", "mid") == "中餐厅"
    assert name_of("05", "big") == "餐饮服务"
    assert label_of("05", "big") == "餐饮服务（05）"
    assert label_of("0501", "mid") == "中餐厅（0501）"
    # 咖啡厅中类与星巴克/上岛小类
    assert name_of("0505", "mid") == "咖啡厅"
    assert name_of("050501") == "星巴克咖啡"
    assert name_of("050502") == "上岛咖啡"
