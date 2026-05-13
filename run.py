from __future__ import annotations

import argparse
from datetime import datetime

from quant.app import (
    backfill_stock_history,
    backfill_etf_history,
    build_stale_report,
    build_app,
    fast_update_data,
    generate_daily_status_report,
    generate_data_inventory,
    generate_recommendations,
    plot_symbol_chart,
    repair_recent_data,
    retry_failed_symbols,
    run_backtest,
    run_experiment,
    sweep_strategy_params,
    update_data,
    update_sz_stock_data,
)
from quant.config import load_config
from quant.dashboard import build_dashboard, serve_dashboard


def main() -> None:
    parser = argparse.ArgumentParser(description="A-share daily quant system")
    parser.add_argument(
        "command",
        choices=["update", "fast_update", "fast_daily", "update_sz", "experiment", "backtest", "recommend", "daily", "status", "plot", "inventory", "backfill_stocks", "backfill_etfs", "sweep", "stale", "retry_failed", "repair_recent", "dashboard"],
        help="Run data update, strategy experiment, backtest, or recommendation report",
    )
    parser.add_argument("--config", default="config/config.yml", help="Config path")
    parser.add_argument("--symbol", default="", help="Symbol for plot command")
    parser.add_argument("--days", type=int, default=120, help="Chart lookback days")
    parser.add_argument("--max-symbols", type=int, default=200, help="Max symbols for partial Shenzhen update")
    parser.add_argument("--start-date", default="2000-01-01", help="History start date for backfill commands")
    parser.add_argument("--only-missing", action="store_true", help="Only fetch symbols without local daily csv")
    parser.add_argument("--workers", type=int, default=4, help="Concurrent workers for backfill commands")
    parser.add_argument("--lookback-days", type=int, default=7, help="Lookback days for fast update commands")
    parser.add_argument("--host", default="127.0.0.1", help="Host for dashboard server")
    parser.add_argument("--port", type=int, default=8000, help="Port for dashboard server")
    parser.add_argument("--serve", action="store_true", help="Serve dashboard after building it")
    parser.add_argument("--public", action="store_true", help="Bind dashboard server to 0.0.0.0 for remote access")
    parser.add_argument("--token", default="", help="Access token for remote dashboard serving")
    args = parser.parse_args()

    config = load_config(args.config)
    ctx = build_app(config)

    if args.command == "update":
        result = update_data(ctx)
        print(result)
    elif args.command == "fast_update":
        result = fast_update_data(ctx, workers=args.workers, lookback_days=args.lookback_days)
        print(result)
    elif args.command == "update_sz":
        result = update_sz_stock_data(ctx, max_symbols=args.max_symbols)
        print(result)
    elif args.command == "experiment":
        ranking = run_experiment(ctx)
        print(ranking.head(10).to_string(index=False))
    elif args.command == "backtest":
        returns_df, _ = run_backtest(ctx)
        print(returns_df.tail(5).to_string(index=False))
        print("=== Backtest Summary ===")
        import pandas as pd
        summary_df = pd.read_csv(ctx.storage.outputs_dir / "backtest_summary.csv")
        print(summary_df.to_string(index=False))
    elif args.command == "recommend":
        ranking, actions = generate_recommendations(ctx)
        print("=== Top Recommendations ===")
        print(ranking.to_string(index=False))
        print("=== Holdings Review ===")
        print(actions.to_string(index=False))
    elif args.command == "daily":
        result = update_data(ctx)
        ranking, actions = generate_recommendations(ctx)
        _, summary_df = generate_data_inventory(ctx)
        status_path = generate_daily_status_report(ctx, update_result=result, ranking=ranking, inventory_summary=summary_df)
        print(result)
        print("=== Top Recommendations ===")
        print(ranking.to_string(index=False))
        print("=== Holdings Review ===")
        print(actions.to_string(index=False))
        print("=== Daily Status ===")
        print(status_path)
    elif args.command == "fast_daily":
        result = fast_update_data(ctx, workers=args.workers, lookback_days=args.lookback_days)
        ranking, actions = generate_recommendations(ctx)
        _, summary_df = generate_data_inventory(ctx)
        status_path = generate_daily_status_report(ctx, update_result=result, ranking=ranking, inventory_summary=summary_df)
        print(result)
        print("=== Top Recommendations ===")
        print(ranking.to_string(index=False))
        print("=== Holdings Review ===")
        print(actions.to_string(index=False))
        print("=== Daily Status ===")
        print(status_path)
    elif args.command == "status":
        ranking, _ = generate_recommendations(ctx)
        _, summary_df = generate_data_inventory(ctx)
        status_path = generate_daily_status_report(ctx, ranking=ranking, inventory_summary=summary_df)
        print(status_path)
    elif args.command == "plot":
        if not args.symbol:
            raise SystemExit("--symbol is required for plot")
        path = plot_symbol_chart(ctx, symbol=args.symbol, days=args.days)
        print(path)
    elif args.command == "inventory":
        detail_df, summary_df = generate_data_inventory(ctx)
        print("=== Data Inventory Summary ===")
        print(summary_df.to_string(index=False))
        print("=== Data Inventory Detail Preview ===")
        preview_cols = ["symbol", "name", "asset_type", "market", "rows", "start_date", "end_date", "updated_at"]
        print(detail_df[preview_cols].head(20).to_string(index=False))
        print("Saved:")
        print(ctx.storage.outputs_dir / "data_inventory_summary.csv")
        print(ctx.storage.outputs_dir / "data_inventory_detail.csv")
    elif args.command == "backfill_stocks":
        start_date = datetime.strptime(args.start_date, "%Y-%m-%d").date()
        result = backfill_stock_history(
            ctx,
            start_date=start_date,
            max_symbols=args.max_symbols if args.max_symbols > 0 else None,
            only_missing=args.only_missing,
            workers=args.workers,
        )
        print(result)
    elif args.command == "backfill_etfs":
        start_date = datetime.strptime(args.start_date, "%Y-%m-%d").date()
        result = backfill_etf_history(
            ctx,
            start_date=start_date,
            max_symbols=args.max_symbols if args.max_symbols > 0 else None,
            only_missing=args.only_missing,
            workers=args.workers,
        )
        print(result)
    elif args.command == "sweep":
        sweep_df = sweep_strategy_params(ctx)
        print(sweep_df.head(20).to_string(index=False))
    elif args.command == "stale":
        detail_df, summary_df, reference_date = build_stale_report(ctx)
        print(f"reference_date={reference_date}")
        print(summary_df.to_string(index=False))
        print("=== Stale Symbols Preview ===")
        print(detail_df.head(30).to_string(index=False))
    elif args.command == "retry_failed":
        result = retry_failed_symbols(ctx, workers=args.workers, lookback_days=args.lookback_days)
        print(result)
    elif args.command == "repair_recent":
        start_date = datetime.strptime(args.start_date, "%Y-%m-%d").date()
        result = repair_recent_data(
            ctx,
            start_date=start_date,
            max_symbols=args.max_symbols if args.max_symbols > 0 else None,
            workers=args.workers,
        )
        print(result)
    elif args.command == "dashboard":
        build_stale_report(ctx)
        if not (ctx.storage.outputs_dir / "daily_recommendations.csv").exists():
            generate_recommendations(ctx)
        if not (ctx.storage.outputs_dir / "data_inventory_summary.csv").exists():
            _, summary_df = generate_data_inventory(ctx)
        else:
            summary_df = None
        if not (ctx.storage.outputs_dir / "daily_status.md").exists():
            ranking = None
            if not (ctx.storage.outputs_dir / "daily_recommendations.csv").exists():
                ranking, _ = generate_recommendations(ctx)
            generate_daily_status_report(ctx, ranking=ranking, inventory_summary=summary_df)
        dashboard_path = build_dashboard(ctx)
        print(dashboard_path)
        if args.serve:
            dashboard_cfg = ctx.config.section("dashboard")
            host = "0.0.0.0" if args.public else args.host
            if args.host == "127.0.0.1" and dashboard_cfg.get("host") and not args.public:
                host = str(dashboard_cfg.get("host"))
            port = args.port if args.port != 8000 else int(dashboard_cfg.get("port", 8000))
            token = args.token or str(dashboard_cfg.get("access_token", ""))
            serve_dashboard(ctx, dashboard_path.parent, host=host, port=port, access_token=token)


if __name__ == "__main__":
    main()
