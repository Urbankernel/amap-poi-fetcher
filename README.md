# amap-poi-fetcher

高德地图 POI 批量获取工具：给定一个空间范围 + 一组高德 Web 服务 key + POI 类型编码，**不遗漏、不重复**地抓全范围内 POI，输出 CSV / GeoJSON / GeoPackage（三图层：POI 点 + 研究范围面 + 渔网网格面）/ Shapefile / 天地图底图交互 HTML。

## 特性

- **四种范围输入**：圆形缓冲（点+半径）/ 行政区（adcode 或名称）/ 经纬度矩形（bbox）/ 本地矢量文件（GeoJSON 原生支持，shp/gpkg/kml 走可选 geopandas）
- **递归四分防截断**：单网格 POI 数达到阈值（默认 200）自动四分裂，直到低于阈值或触底（最大深度/最小网格双保险）
- **多 key 轮换 + QPS 退避**：key 失效/配额耗尽自动切换；QPS 限流与网络异常指数退避重试，失败原因全记录
- **范围过滤 + 跨网格去重**：按范围几何 covers 过滤网格矩形带回的界外点；按 `poi_id` 全局去重，消除相邻网格边界重复
- **断点续跑**：增量落盘（每网格 flush）+ `state.json` 进度快照，中断后 `--resume` 接着跑
- **轻依赖**：核心仅 `requests` / `pyyaml` / `shapely` / `folium` 四个包；GPKG/SHP 导出与 shp/gpkg 范围导入走可选 geopandas
- **坐标系规范**：内部与成果统一 WGS-84，同时保留 GCJ-02 原始坐标列；**发请求前自动将网格角点 WGS-84 → GCJ-02**（高德 API 输入坐标要求火星坐标），确保搜索窗与成果网格精确重合

## 安装

```bash
git clone https://github.com/Urbankernel/amap-poi-fetcher.git
cd amap-poi-fetcher
pip install -r requirements.txt        # 或 pip install .
```

要求 Python ≥ 3.9。需要导入 shp/gpkg/kml 范围文件时再装可选依赖：

```bash
pip install -r requirements-optional.txt   # geopandas
```

## 准备工作

1. **高德 Web 服务 key**：到 [高德开放平台](https://lbs.amap.com/) 创建「Web 服务」类型应用取 key。单 key 配额有限（个人开发者通常 5000 次/日），大区域务必准备多个。
2. **行政区 adcode**：在 [高德行政区编码表](https://lbs.amap.com/api/webservice/download) 查询（如上海浦东新区 `310115`）。也可以直接用行政区名称。
3. **POI 类型编码**：参考官方《POI 分类编码表》。**尽量用小类**（如 `050301` 肯德基），传大类极易触发超限。多类型用逗号分隔。
4. **（可选）底图凭证**：交互地图默认用天地图底图，tk 三级来源（优先级从高到低）：配置文件 `viz.tianditu_tk` > `keys.txt` 的 `tk=` 行 > 环境变量 `TIANDITU_TK`（免费到 [天地图控制台](https://console.tianditu.gov.cn/) 申请）。无 tk 时降级 CartoDB Positron 底图，而 CartoDB 现也需 key（三级来源：`viz.carto_key` > `keys.txt` 的 `carto=` 行 > 环境变量 `CARTO_KEY`，免费申请 https://carto.com/basemaps/apikey/），无 key 时 CartoDB 瓦片带 "API key required" 水印。

## 配置

复制 `configs/example.yaml` 改成自己的配置。关键字段：

| 字段 | 说明 | 默认 |
|------|------|------|
| `keys` / `key_file` / 环境变量 `AMAP_KEYS` | 高德 key（三选一，优先级从高到低） | — |
| `types` | POI 类型编码，逗号分隔多类型 | 必填 |
| `study_area.type` | `buffer` / `admin` / `bbox` / `file` 四选一 | 必填 |
| `filter_by_scope` | 入库前按范围几何过滤范围外 POI（网格是矩形，边界网格会带回范围外的点） | true |
| `grid.size` | 初始网格边长（度） | 0.1 |
| `grid.split_threshold` | 单网格 POI 数达到此值即递归四分 | 200 |
| `grid.max_depth` / `grid.min_size` | 递归最大深度 / 最小网格边长（度） | 4 / 0.005 |
| `request.offset` | 每页条数（高德上限 25） | 20 |
| `request.qps` | 每秒请求数上限（相邻请求间隔 ≥ 1/qps） | 2 |
| `request.sleep_between_pages` | 分页间隔（秒） | 1.0 |
| `request.max_retries` | 单请求最大重试次数（网络异常 / QPS 限流，指数退避） | 5 |
| `request.retry_base_seconds` | 退避基数（秒）：等待 = base × 2^n + 随机抖动 | 1.0 |
| `request.timeout` | 单次请求超时（秒） | 10.0 |
| `outputs.dir` | run 目录根 | runs |
| `viz.basemap` | `tianditu` / `cartodb`（tianditu 无 tk 时自动降级 CartoDB Positron，其需另配 `carto_key`） | tianditu |
| `viz.tianditu_tk` | 天地图 tk（三级来源：此字段 > `key_file` 的 `tk=` 行 > 环境变量 `TIANDITU_TK`）；有 tk 时提供天地图矢量/影像底图 + 矢量注记/影像注记叠加层 | "" |
| `viz.carto_key` | CartoDB 底图 key（三级来源：此字段 > `key_file` 的 `carto=` 行 > 环境变量 `CARTO_KEY`）；无 key 时 CartoDB 瓦片带 "API key required" 水印 | "" |
| `viz.poi_colors.vec` / `.img` | 多类型 POI 按类别着色：分类粒度自动跟随 `types` 编码层级（2 位大类 / 4 位中类 / 6 位小类）；类别 = 最终获取的类别（编码升序取色），颜色数量可按类别数任意增减，超出时循环复用并日志提示 | 各 5 色 |

范围四种写法示例：

```yaml
study_area: {type: admin, adcode: "310115"}                       # 行政区
study_area: {type: buffer, center: [121.544, 31.221], radius_km: 5}  # 圆形缓冲
study_area: {type: bbox, bounds: [121.45, 30.77, 122.01, 31.39]}  # 矩形
study_area: {type: file, path: "examples/study_area.geojson"}     # 本地文件
```

## 用法

```bash
# 干跑估算：只解析范围、划网格、估算请求数（不发 POI 请求）
amap-poi estimate -c configs/example.yaml

# 执行抓取
amap-poi run -c configs/example.yaml

# 断点续跑（中断/失败/key 耗尽后）
amap-poi run -c configs/example.yaml --resume runs/20260921_143000_浦东新区_050301

# CLI 覆盖配置（小样验证常用）
amap-poi run -c configs/example.yaml --adcode 310115 --types 050301 --grid-size 0.2
```

未安装包时也可直接用源码跑：`python -m src.amap_poi_fetcher.cli run -c ...`（或 `pip install -e .` 后用 `amap-poi`）。

## 输出

每次运行生成独立 run 目录：

```
runs/20260921_143000_浦东新区_050301/
├── config.snapshot.yaml    # 配置快照（key 已脱敏，续跑校验用）
├── pois_incremental.csv    # 增量落盘（每网格完成即写入）
├── state.json              # 调度状态（断点续跑依据）
├── failed_grids.jsonl      # 失败网格（含原因，可复跑）
├── truncated_grids.jsonl   # 触底仍超限网格（疑似截断 ⚠️）
├── run.log                 # 完整日志
└── outputs/
    ├── pois.csv            # 最终 CSV（utf-8-sig，Excel 直开不乱码）
    ├── pois.geojson        # WGS-84 FeatureCollection
    ├── pois.gpkg           # GeoPackage（默认开启，需 geopandas）：三图层
    │                       #   pois（POI 点）+ study_area（研究范围面）+ grids（渔网网格面）
    ├── pois.shp            # Shapefile（outputs.shp: true 时，需 geopandas）：POI 点
    ├── study_area.shp      # Shapefile（outputs.shp: true 时，需 geopandas）：研究范围面
    ├── grids.shp           # Shapefile（outputs.shp: true 时，需 geopandas）：渔网网格面
    └── map.html            # 天地图底图交互地图（自包含单文件）
```

CSV 字段：`poi_id, lon（WGS-84）, lat（WGS-84）, lon_gcj02（火星坐标原值）, lat_gcj02（火星坐标原值）, name, poi_type, poi_type_code, cityname, adname, address, grid_id（来源网格，溯源用）`

网格图层字段：`gid（网格 ID，四分象限后缀可溯源，与 CSV 的 grid_id 对应）, depth（递归细分深度）, truncated（1=触底仍超限的疑似截断网格）`

矢量成果说明：GPKG/GeoJSON 字段与 CSV 完全一致；SHP 受 10 字符字段名限制，仅 `poi_type_code` → `poi_code`，编码 UTF-8（附 .cpg）。GPKG/SHP 导出依赖可选包 geopandas（`pip install -r requirements-optional.txt`），缺包时告警跳过、不影响其他产物。

CSV 打开提示：`poi_type_code`（如 `050301`）在 CSV 中存的是字符串、前导 0 完整；但 Excel 或 `pandas.read_csv` 会自动把它识别为数字、丢失前导 0。请在 Excel 中用「数据 → 从文本/CSV」导入并把该列设为文本，或直接使用 GPKG/GeoJSON（该字段本就是字符串类型，前导 0 永不丢失）。

## 目录结构

```
amap-poi-fetcher/
├── configs/example.yaml      # 配置示例
├── src/amap_poi_fetcher/
│   ├── cli.py                # 命令行入口（run / estimate / --resume）
│   ├── config.py             # YAML 加载 + CLI 覆盖 + 校验
│   ├── client.py             # 高德 API 封装（district / place-polygon，分页迭代）
│   ├── keys.py               # KeyPool：轮换、失效标记、全灭检测
│   ├── retry.py              # 指数退避
│   ├── coords.py             # GCJ-02 → WGS-84
│   ├── scope/                # 范围四模式：buffer / admin / bbox / file
│   ├── grid.py               # 渔网划分 + 四分细分
│   ├── scheduler.py          # 主调度：递归四分、分页、去重、进度保存
│   ├── dedup.py              # poi_id 全局去重
│   ├── store.py              # 增量落盘、state.json、失败/截断记录
│   ├── export.py             # CSV / GeoJSON 导出
│   ├── viz.py                # folium 交互地图（天地图底图）
│   └── log.py                # 日志（控制台 + 文件双通道）
├── tests/                    # pytest（含脏数据与递归四分集成测试）
└── docs/方案设计.md           # 设计文档
```

## 配额估算

请求数 ≈ **Σ 每个网格的翻页数**（每网格至少 1 次探测请求；超限网格四分裂后子网格各自再计）。`amap-poi estimate` 可先看网格规模。经验：中心城区小类类型，0.1° 网格通常 1~3 页；大类类型极易超限，请拆小类。

## 常见问题

- **日志出现「触底仍超限」**：该网格细分到最大深度仍 ≥ 阈值，结果被截断并记入 `truncated_grids.jsonl`。对策：调小 `grid.size`、调大 `grid.max_depth`，或把类型拆得更细后重跑。
- **「所有 key 均不可用」**：进度已保存。补充新 key 到配置后用 `--resume` 续跑即可，已抓数据不会重抓（按 `poi_id` 去重）。
- **QPS 报错**：调低 `request.qps` 或调大 `request.retry_base_seconds`。
- **失败网格复跑**：`failed_grids.jsonl` 中的网格在 `--resume` 时会自动重试（它们不在已完成清单里）。
- **Excel 打开 CSV 后 `poi_type_code` 前导 0 丢失**：CSV 本身存的是字符串 `050301`，是 Excel 自动把它当成了数字。用「数据 → 从文本/CSV」导入并把该列设为文本，或改用 GPKG/GeoJSON（该字段本就是字符串类型）。
- **抓取的 POI 数量/类别与高德地图检索对不上**：除 typecode 本身问题外，常见原因如下：
  1. **typecode 录入粗粒度/错误**：如肯德基被录成大类 `050000` 而非小类 `050301`，用 `types=050301` 检索时这类店匹配不到、直接漏掉；可用高德开放平台的「ID 查询搜索」API 核实单店 typecode。
  2. **typecode 多编码拼接**：一条 POI 的 typecode 可能是 `"050301|050200"`（竖线拼接多个类）。指定大类检索时，其所属中类、小类 POI 也会返回，但不保证完整；需更精确时推荐输入更小的类或缩小范围（官方文档说明）。
  3. **收录/更新延迟**：新开店未收录、关店未下架、搬迁未更新，Web 服务 API 的索引与高德 App 前端并非实时同步，两个入口结果可能对不上。
  4. **触底截断**：网格细分到 `max_depth`/`min_size` 仍超阈值时，超出 API 单次返回上限的部分拿不到，记入 `truncated_grids.jsonl`（见上「触底仍超限」）。
  5. **边界网格漏抓**：请求前网格角点 WGS-84 → GCJ-02 转换的微小误差，使搜索窗口在边界处略偏移，恰好压在边界上的 POI 可能落窗外被漏。
  6. **`filter_by_scope` 误滤**：入库前用几何 `covers` 过滤范围外点，对不规则/凹形范围，边界判定可能把少量边缘点误判为「范围外」丢弃。

## 合规声明

- key / tk / CartoDB key 属个人凭证，请通过 `key_file`（`keys.txt`）或环境变量提供，**不要提交到 git**（`.gitignore` 已排除 `keys.txt` / `.env` / `runs/`）。
- POI 数据获取与使用须遵守[高德开放平台服务条款](https://lbs.amap.com/)，仅供学习与研究用途，请控制请求频率。

## License

MIT
