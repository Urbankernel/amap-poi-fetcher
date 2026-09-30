"""amap-poi-fetcher：高德地图 POI 批量获取工具。

特性：四模式范围输入（buffer/admin/bbox/file）、递归四分防截断、
多 key 轮换与 QPS 退避、跨网格去重、增量落盘与断点续跑、
CSV/GeoJSON/天地图交互 HTML 三件套输出。
"""

__version__ = "0.1.0"
