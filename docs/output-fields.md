# POI 输出字段说明

本文档罗列 amap-poi-fetcher 抓取结果中**每一个输出字段的名称、含义、来源与取值说明**，供城市规划、商业选址等分析场景查阅。

## 一、总览

- 工具请求高德地图 **v3 `/place/polygon`** 接口，且使用 `extensions=all`（保证 `cityname`、`adname` 等行政区与扩展字段可用）。
- 输出共 **37 个字段**，其中 36 个来自高德 API 返回，1 个（`grid_id`）为工具自增的网格溯源 ID。
- 所有字段在所有产物（CSV / GeoJSON / GPKG）中名称一致；仅 **SHP** 因格式限制（字段名 ≤ 10 字符）对部分字段做了改名（见[第四节](#四shp-字段名映射)）。

### 坐标系约定

| 字段 | 坐标系 | 说明 |
|---|---|---|
| `lon` / `lat` | **WGS-84** | 工具用 `gcj02_to_wgs84` 转换后的坐标，可直接叠加在天地图/WGS-84 底图上 |
| `lon_gcj02` / `lat_gcj02` | **GCJ-02** | 高德 API 返回的原始坐标（火星坐标），与高德/天地图拾取坐标同系 |
| `entr_location` | **GCJ-02** | 入口坐标，高德原始值，**未做** WGS-84 转换 |

> 为什么同时保留两套坐标：国内地图底图多用 GCJ-02，而 GeoJSON/GPKG 规范及 GIS 分析通常要求 WGS-84，两套并存可无缝衔接两类场景。

---

## 二、字段详解

### 1. 基础字段（11 个）

| 字段 | 含义 | API 来源 | 扩展级别 | 说明 |
|---|---|---|---|---|
| `poi_id` | POI 唯一 ID | `id` | base | 去重主键 |
| `lon` | 经度（WGS-84） | `location` | base | GCJ-02 转 WGS-84 后，保留 7 位小数 |
| `lat` | 纬度（WGS-84） | `location` | base | 同上 |
| `lon_gcj02` | 经度（GCJ-02 原值） | `location` | base | 高德原始坐标 |
| `lat_gcj02` | 纬度（GCJ-02 原值） | `location` | base | 高德原始坐标 |
| `name` | POI 名称 | `name` | base | 如「肯德基(人民广场店)」 |
| `poi_type` | 类型整串 | `type` | base | 格式 `大类;中类;小类`，如 `餐饮服务;快餐厅;肯德基` |
| `poi_type_code` | 类型编码 | `typecode` | base | 如 `050301`，对应 `data/poi_types.json` 码表 |
| `cityname` | 城市名 | `cityname` | all | 如「上海市」 |
| `adname` | 区县名 | `adname` | all | 如「黄浦区」 |
| `address` | 详细地址 | `address` | base | 如「西藏中路 100 号」 |

### 2. 行政区层级（4 个）

| 字段 | 含义 | API 来源 | 扩展级别 | 说明 |
|---|---|---|---|---|
| `pname` | 省份名 | `pname` | all | 如「上海市」 |
| `pcode` | 省份编码 | `pcode` | all | 如 `310000` |
| `citycode` | 城市编码 | `citycode` | all | 城市级行政区划代码 |
| `adcode` | 区县编码 | `adcode` | all | 区县级行政区划代码，编码比名称更稳定，便于跨表关联 |

> `pname/pcode/adcode/citycode` 与 `adname/cityname` 互补：名称直观、编码稳定，做行政区统计时建议用编码列关联。

### 3. 联系与标识（5 个）

| 字段 | 含义 | API 来源 | 扩展级别 | 说明 |
|---|---|---|---|---|
| `tel` | 电话 | `tel` | base | 高德可能返回**数组**（多个号码），工具用逗号拼接为单个字符串 |
| `alias` | 别名 | `alias` | all | 同样可能为数组，逗号拼接；可用于品牌名归一（如「肯德基」vs「KFC」） |
| `biz_type` | 行业类型 | `biz_type` | base | 与 `typecode` 不同口径的行业分类，可能为数组，逗号拼接 |
| `parent` | 父 POI ID | `parent` | base | 指向所属商场/写字楼等父级 POI，可能为空 |
| `navi_poiid` | 导航 POI ID | `navi_poiid` | all | 用于接导航/路线规划/可达性分析 |

### 4. 商业分析（4 个）

| 字段 | 含义 | API 来源 | 扩展级别 | 说明 |
|---|---|---|---|---|
| `business_area` | 所属商圈 | `business_area` | all | 商业选址黄金字段，判断 POI 落位哪个商圈 |
| `tag` | 特色内容 | `tag` | all | 美食类特色菜等业态细分，可能为数组 |
| `biz_rating` | 评分 | `biz_ext.rating` | all | 从嵌套对象 `biz_ext` 展平，消费活力/口碑指标 |
| `biz_cost` | 人均消费 | `biz_ext.cost` | all | 从 `biz_ext` 展平，判断消费档次、商圈定位 |

### 5. 图片（2 个）

| 字段 | 含义 | API 来源 | 扩展级别 | 说明 |
|---|---|---|---|---|
| `photo_title` | 首图标题 | `photos[0].title` | all | 仅取**第一张**图片的标题 |
| `photo_url` | 首图链接 | `photos[0].url` | all | 仅取第一张图片的下载链接，只存 URL 不下载 |

> 高德每个 POI 可能返回多张照片；当前单表结构下只保留第一张。如需完整多图，需另行扩展为一对多关联表。

### 6. 建筑 / 楼层 / 停车 / 入口（5 个）

| 字段 | 含义 | API 来源 | 扩展级别 | 说明 |
|---|---|---|---|---|
| `cpid` | 父级 POI ID | `cpid` | all | 商铺 → 所在建筑物的归属关系 |
| `floor` | 楼层索引 | `floor` | all | 商场内 POI 楼层 |
| `truefloor` | 所在楼层 | `truefloor` | all | 显示楼层名，如 `F1` |
| `parking_type` | 停车场类型 | `parking_type` | all | 仅停车场类 POI 有值 |
| `entr_location` | 入口经纬度 | `entr_location` | all | GCJ-02 坐标，**未转** WGS-84，格式 `经度,纬度` |

### 7. 联系信息与邮编（3 个）

| 字段 | 含义 | API 来源 | 扩展级别 | 说明 |
|---|---|---|---|---|
| `website` | 网址 | `website` | all | 可能为数组，逗号拼接 |
| `email` | 邮箱 | `email` | all | 可能为数组，逗号拼接 |
| `postcode` | 邮编 | `postcode` | all | 使用频率较低 |

### 8. 室内地图（2 个）

| 字段 | 含义 | API 来源 | 扩展级别 | 说明 |
|---|---|---|---|---|
| `indoor_map` | 室内地图标志 | `indoor_map` | all | 数字可能被转成字符串（如 `1`/`0`） |
| `indoor_data` | 室内地图数据 | `indoor_data` | all | 嵌套对象整体序列化为 JSON 字符串存储 |

### 9. 网格溯源（1 个）

| 字段 | 含义 | API 来源 | 扩展级别 | 说明 |
|---|---|---|---|---|
| `grid_id` | 渔网网格 ID | —（工具自增） | — | 记录该 POI 由哪个网格抓取，含四分象限后缀，可溯源到具体子网格 |

---

## 三、字段类型与特殊处理规则

工具在 `poi_to_row()` 中对高德可能返回「非字符串」的字段做了统一归一，避免 CSV 导出时出现 `[]`、数字等非预期值：

| 处理规则 | 涉及字段 |
|---|---|
| 数组 → 逗号拼接字符串 | `tel`、`alias`、`biz_type`、`website`、`email`、`tag` |
| 数字 → 字符串 | `indoor_map`、`biz_rating`、`biz_cost` |
| 嵌套对象 → 展平 | `biz_ext.rating` → `biz_rating`、`biz_ext.cost` → `biz_cost` |
| 数组取首元素 | `photos` → `photo_title` / `photo_url` |
| 嵌套对象 → JSON 字符串 | `indoor_data` |
| 空值（`None` / `[]` / 空串）→ 空字符串 | 所有字段 |

---

## 四、SHP 字段名映射

SHP 格式（Shapefile 的 DBF）字段名上限为 10 字符，以下 6 个超长字段在 SHP 导出时会改名；CSV / GeoJSON / GPKG 保持原字段名不变。

| 通用字段名 | SHP 字段名 |
|---|---|
| `poi_type_code` | `poi_code` |
| `business_area` | `biz_area` |
| `photo_title` | `photo_titl` |
| `parking_type` | `park_type` |
| `entr_location` | `entr_loc` |
| `indoor_data` | `indoor_dat` |

---

## 五、未保留的 API 字段

高德 `extensions=all` 返回的部分字段**未纳入输出**，如需补充可再扩展（详见仓库提交记录中的讨论）：

- **官方标注逐渐废弃**：`groupbuy_num`、`discount_num`、`meal_ordering`、`seat_ordering`、`ticket_ordering`、`hotel_ordering`
- **polygon 搜索无值**：`distance`（仅周边搜索 `around` 有值）
- **官方不返回内容**：`exit_location`
- **高德内部格**：`gridcode`
