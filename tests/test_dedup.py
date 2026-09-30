"""去重测试：判重语义、CSV 重建。"""

import csv

from amap_poi_fetcher.dedup import PoiDedup


def test_add_semantics():
    d = PoiDedup()
    assert d.add("A1") is True
    assert d.add("A1") is False  # 重复
    assert d.add("A2") is True
    assert len(d) == 2
    assert "A1" in d


def test_load_csv_rebuild(tmp_path):
    csv_path = tmp_path / "pois.csv"
    with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=["poi_id", "name"])
        w.writeheader()
        w.writerow({"poi_id": "B1", "name": "x"})
        w.writerow({"poi_id": "B2", "name": "y"})
    d = PoiDedup()
    assert d.load_csv(csv_path) == 2
    assert d.add("B1") is False  # 已存在
    assert d.add("B3") is True


def test_load_csv_missing_file(tmp_path):
    d = PoiDedup()
    assert d.load_csv(tmp_path / "nonexistent.csv") == 0
