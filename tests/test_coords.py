"""坐标转换测试：性质断言（境外透传、境内偏移幅度、确定性、往返）。"""

from amap_poi_fetcher.coords import gcj02_to_wgs84, wgs84_to_gcj02


def test_out_of_china_passthrough():
    # 境外坐标无火星偏移，原样返回
    assert gcj02_to_wgs84(0.0, 0.0) == (0.0, 0.0)
    assert gcj02_to_wgs84(-73.98, 40.75) == (-73.98, 40.75)  # 纽约
    assert wgs84_to_gcj02(-73.98, 40.75) == (-73.98, 40.75)


def test_china_coord_offset_reasonable():
    # 北京 GCJ-02 → WGS-84：应向西向南偏移，幅度在百米级（< 0.01°）
    lng, lat = gcj02_to_wgs84(116.404, 39.915)
    assert abs(lng - 116.404) < 0.01
    assert abs(lat - 39.915) < 0.01
    assert lng != 116.404 or lat != 39.915  # 境内必须有偏移


def test_deterministic():
    a = gcj02_to_wgs84(121.4737, 31.2304)  # 上海
    b = gcj02_to_wgs84(121.4737, 31.2304)
    assert a == b


def test_wgs84_to_gcj02_offset_direction():
    # WGS-84 → GCJ-02：境内应向东北偏移（正值），与反向偏移方向相反
    lng, lat = wgs84_to_gcj02(116.404, 39.915)
    assert lng > 116.404 and lat > 39.915
    # 五台县一带的偏移量（本工具实际场景）：经度约 +0.0067°、纬度约 +0.0007°
    wl, wl_lat = wgs84_to_gcj02(113.25, 38.72)
    assert 0.005 < wl - 113.25 < 0.009
    assert 0 < wl_lat - 38.72 < 0.002


def test_roundtrip():
    # WGS → GCJ → WGS 往返误差应米级（< 1e-4°）
    for lng, lat in [(116.404, 39.915), (113.25, 38.72), (121.4737, 31.2304)]:
        g_lng, g_lat = wgs84_to_gcj02(lng, lat)
        back_lng, back_lat = gcj02_to_wgs84(g_lng, g_lat)
        assert abs(back_lng - lng) < 1e-4
        assert abs(back_lat - lat) < 1e-4
