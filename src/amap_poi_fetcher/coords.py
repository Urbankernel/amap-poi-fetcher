"""GCJ-02（火星坐标系）↔ WGS-84 坐标转换。

公式移植自原始 notebook 脚本（经验公式 + 对称反向逼近），数值行为与原脚本一致。
纪律：GCJ-02 只用于「向高德发请求 / 接收响应」那一刻，系统内部与成果文件一律 WGS-84。
"""

from __future__ import annotations

import math

PI = 3.1415926535897932384626  # π
A = 6378245.0  # 长半轴
EE = 0.00669342162296594323  # 偏心率平方


def _out_of_china(lng: float, lat: float) -> bool:
    """是否在中国境外（境外坐标无火星偏移，原样返回）。"""
    return not (73.66 < lng < 135.05 and 3.86 < lat < 53.55)


def _transform_lat(lng: float, lat: float) -> float:
    """纬度偏移辅助计算（多项式 + 三角函数经验公式）。"""
    ret = -100.0 + 2.0 * lng + 3.0 * lat + 0.2 * lat * lat + \
          0.1 * lng * lat + 0.2 * math.sqrt(math.fabs(lng))
    ret += (20.0 * math.sin(6.0 * lng * PI) + 20.0 *
            math.sin(2.0 * lng * PI)) * 2.0 / 3.0
    ret += (20.0 * math.sin(lat * PI) + 40.0 *
            math.sin(lat / 3.0 * PI)) * 2.0 / 3.0
    ret += (160.0 * math.sin(lat / 12.0 * PI) + 320 *
            math.sin(lat * PI / 30.0)) * 2.0 / 3.0
    return ret


def _transform_lng(lng: float, lat: float) -> float:
    """经度偏移辅助计算（多项式 + 三角函数经验公式）。"""
    ret = 300.0 + lng + 2.0 * lat + 0.1 * lng * lng + \
          0.1 * lng * lat + 0.1 * math.sqrt(math.fabs(lng))
    ret += (20.0 * math.sin(6.0 * lng * PI) + 20.0 *
            math.sin(2.0 * lng * PI)) * 2.0 / 3.0
    ret += (20.0 * math.sin(lng * PI) + 40.0 *
            math.sin(lng / 3.0 * PI)) * 2.0 / 3.0
    ret += (150.0 * math.sin(lng / 12.0 * PI) + 300.0 *
            math.sin(lng / 30.0 * PI)) * 2.0 / 3.0
    return ret


def gcj02_to_wgs84(lng: float, lat: float) -> tuple[float, float]:
    """GCJ-02 → WGS-84。

    参数:
        lng: 经度（GCJ-02）
        lat: 纬度（GCJ-02）

    返回:
        (经度, 纬度)，WGS-84。境外坐标原样返回。
    """
    if _out_of_china(lng, lat):
        return lng, lat
    dlat = _transform_lat(lng - 105.0, lat - 35.0)
    dlng = _transform_lng(lng - 105.0, lat - 35.0)
    radlat = lat / 180.0 * PI
    magic = math.sin(radlat)
    magic = 1 - EE * magic * magic
    sqrtmagic = math.sqrt(magic)
    dlat = (dlat * 180.0) / ((A * (1 - EE)) / (magic * sqrtmagic) * PI)
    dlng = (dlng * 180.0) / (A / sqrtmagic * math.cos(radlat) * PI)
    mglat = lat + dlat
    mglng = lng + dlng
    # 利用对称原理反向逼近 WGS-84
    return lng * 2 - mglng, lat * 2 - mglat


def wgs84_to_gcj02(lng: float, lat: float) -> tuple[float, float]:
    """WGS-84 → GCJ-02（正向偏移，用于向高德发请求的坐标）。

    高德 Web 服务 API 的输入坐标一律要求 GCJ-02。本函数只在「发请求那一刻」使用，
    内部存储与成果文件仍统一 WGS-84。境外坐标原样返回。
    与 gcj02_to_wgs84 近似互逆（往返误差 < 1e-4°，即米级）。
    """
    if _out_of_china(lng, lat):
        return lng, lat
    dlat = _transform_lat(lng - 105.0, lat - 35.0)
    dlng = _transform_lng(lng - 105.0, lat - 35.0)
    radlat = lat / 180.0 * PI
    magic = math.sin(radlat)
    magic = 1 - EE * magic * magic
    sqrtmagic = math.sqrt(magic)
    dlat = (dlat * 180.0) / ((A * (1 - EE)) / (magic * sqrtmagic) * PI)
    dlng = (dlng * 180.0) / (A / sqrtmagic * math.cos(radlat) * PI)
    return lng + dlng, lat + dlat
