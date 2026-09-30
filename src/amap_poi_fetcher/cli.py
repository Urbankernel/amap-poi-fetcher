"""命令行入口。

    amap-poi run -c configs/example.yaml                    # 新跑
    amap-poi run -c configs/example.yaml --resume runs/xxx  # 断点续跑
    amap-poi estimate -c configs/example.yaml               # 干跑估算（不发 POI 请求）
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from . import __version__
from .client import AmapClient
from .config import Config, ConfigError, load_config, type_count
from .export import export_csv, export_geojson, export_gpkg, export_shp
from .grid import fishnet
from .keys import AllKeysExhausted, KeyPool
from .log import attach_file_handler, setup_logging
from .scheduler import Scheduler
from .scope import load_scope
from .store import RunStore

logger = logging.getLogger("amap_poi_fetcher.cli")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="amap-poi",
        description="高德地图 POI 批量获取工具：四模式范围、递归四分防截断、多 key 轮换、断点续跑",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    def add_common(p: argparse.ArgumentParser) -> None:
        p.add_argument("-c", "--config", required=True, help="YAML 配置文件路径")
        p.add_argument("--keys", help="高德 key 列表（逗号分隔，覆盖配置）")
        p.add_argument("--types", help="POI 类型编码（逗号分隔多类型，覆盖配置）")
        p.add_argument("--adcode", help="快捷指定行政区编码（覆盖 study_area）")
        p.add_argument("--bbox", nargs=4, type=float, metavar=("XMIN", "YMIN", "XMAX", "YMAX"),
                       help="快捷指定经纬度矩形（覆盖 study_area）")
        p.add_argument("--grid-size", type=float, help="初始网格边长（度，覆盖配置）")
        p.add_argument("--output-dir", help="run 目录根（覆盖配置 outputs.dir）")

    run_p = sub.add_parser("run", help="执行抓取")
    add_common(run_p)
    run_p.add_argument("--resume", help="从既有 run 目录断点续跑")

    est_p = sub.add_parser("estimate", help="干跑：只解析范围、划网格、估算请求数")
    add_common(est_p)

    return parser


def _overrides(args: argparse.Namespace) -> dict:
    return {
        "keys": args.keys, "types": args.types, "adcode": args.adcode,
        "bbox": args.bbox, "grid_size": args.grid_size, "output_dir": args.output_dir,
    }


def _make_client(cfg: Config, pool: KeyPool) -> AmapClient:
    return AmapClient(
        pool,
        qps=cfg.request.qps,
        max_retries=cfg.request.max_retries,
        retry_base=cfg.request.retry_base_seconds,
        timeout=cfg.request.timeout,
        sleep_between_pages=cfg.request.sleep_between_pages,
    )


def _masked_snapshot(cfg: Config) -> dict:
    """配置快照：key / tk / carto_key 脱敏后写入（仅用于续跑校验，不泄漏凭证）。"""
    snapshot = dict(cfg.raw)
    snapshot["keys"] = [KeyPool.mask(k) for k in cfg.keys]
    snapshot.pop("key_file", None)
    # 天地图 tk / CartoDB key 一并脱敏（可能来自 key_file/环境变量，同样不进快照明文）
    viz = dict(snapshot.get("viz") or {})
    for cred in ("tianditu_tk", "carto_key"):
        if viz.get(cred):
            viz[cred] = KeyPool.mask(str(viz[cred]))
    if viz:
        snapshot["viz"] = viz
    return snapshot


def _check_resume_compat(snapshot: dict, cfg: Config) -> None:
    """续跑前校验关键配置与快照一致（范围/类型/网格参数变了续跑会串数据）。"""
    for key in ("study_area", "types", "grid"):
        if snapshot.get(key) != cfg.raw.get(key):
            raise ConfigError(
                f"续跑配置与快照不一致（{key}）：\n"
                f"  快照: {snapshot.get(key)}\n  当前: {cfg.raw.get(key)}\n"
                f"请用原配置续跑，或另起新任务。"
            )


def cmd_estimate(args: argparse.Namespace) -> int:
    cfg = load_config(args.config, _overrides(args))
    setup_logging()
    pool = KeyPool(cfg.keys)
    client = _make_client(cfg, pool)
    scope = load_scope(cfg.study_area, client)  # admin 模式会消耗 1 次 district 请求

    grids = fishnet(scope.geometry, cfg.grid.size)
    n_types = type_count(cfg.types)
    print(f"范围: {scope.name}")
    print(f"初始网格: {len(grids)} 个（size={cfg.grid.size}°），POI 类型: {n_types} 个")
    print(f"请求数下限: {len(grids)} 次（每网格至少 1 次探测，多类型合并为单次请求）；")
    print(f"实际 ≈ Σ 每个网格的翻页数，超限网格会递归四分继续增加。")
    print(f"提示：个人开发者 key 通常 5000 次/日配额，"
          f"当前配置 {len(cfg.keys)} 个 key，grid.size 调大可减少网格数。")
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    cfg = load_config(args.config, _overrides(args))
    setup_logging()
    pool = KeyPool(cfg.keys)
    client = _make_client(cfg, pool)
    scope = load_scope(cfg.study_area, client)

    state = None
    if args.resume:
        store = RunStore.resume(args.resume)
        _check_resume_compat(store.load_snapshot(), cfg)
        attach_file_handler(store.run_dir)
        state = store.load_state()
        pool.restore(state.get("key_pool", {}))
    else:
        first_type = cfg.types.replace("|", ",").split(",")[0].strip()
        store = RunStore.create(cfg.outputs.dir, f"{scope.name}_{first_type}",
                                _masked_snapshot(cfg))
        attach_file_handler(store.run_dir)

    scheduler = Scheduler(
        client, store,
        types=cfg.types,
        offset=cfg.request.offset,
        extensions=cfg.extensions,
        split_threshold=cfg.grid.split_threshold,
        max_depth=cfg.grid.max_depth,
        min_size=cfg.grid.min_size,
        filter_by_scope=cfg.filter_by_scope,
    )

    try:
        stats = scheduler.run(scope.geometry, cfg.grid.size, state)
    except AllKeysExhausted:
        store.close()
        return 2

    store.close()

    outputs: list[Path] = []
    if cfg.outputs.csv:
        outputs.append(export_csv(store))
    if cfg.outputs.geojson:
        outputs.append(export_geojson(store))
    if cfg.outputs.gpkg:
        gpkg = export_gpkg(store, scope.geometry, scope.name,
                           grids=scheduler.final_grids,
                           truncated_ids=scheduler.truncated_ids)
        if gpkg is not None:
            outputs.append(gpkg)
    if cfg.outputs.shp:
        outputs.extend(export_shp(store, scope.geometry, scope.name,
                                  grids=scheduler.final_grids,
                                  truncated_ids=scheduler.truncated_ids))
    if cfg.outputs.html:
        from .viz import build_map
        outputs.append(build_map(
            geometry=scope.geometry,
            grids=scheduler.final_grids,
            truncated_ids=scheduler.truncated_ids,
            csv_path=store.csv_path,
            out_path=store.outputs_dir / "map.html",
            basemap=cfg.viz.basemap,
            tk=cfg.viz.tianditu_tk or None,
            carto_key=cfg.viz.carto_key or None,
            poi_colors=cfg.viz.poi_colors,
            category_level=cfg.category_level,
            configured_types=cfg.types,
        ))

    print("=" * 50)
    print(f"完成：入库 {stats.kept} 条（拉取 {stats.fetched}，范围外过滤 {stats.filtered}，"
          f"重复去重 {stats.fetched - stats.filtered - stats.kept}）")
    print(f"网格：处理 {stats.grids_done}，四分裂 {stats.grids_split} 次，"
          f"失败 {stats.grids_failed}，疑似截断 {stats.grids_truncated}")
    print(f"实际请求: {client.request_count} 次")
    for p in outputs:
        print(f"产物: {p}")
    if stats.grids_failed:
        print(f"⚠️ 有 {stats.grids_failed} 个失败网格，见 {store.failed_path}，可修复后 --resume 复跑")
    if stats.grids_truncated:
        print(f"⚠️ 有 {stats.grids_truncated} 个触底仍超限网格，见 {store.truncated_path}，"
              f"建议调小 grid.size 或拆更细类型重跑")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "run":
            return cmd_run(args)
        return cmd_estimate(args)
    except ConfigError as e:
        print(f"配置错误: {e}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\n已中断。增量数据与进度均已落盘，可用 --resume 续跑。", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
