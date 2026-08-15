# A-Share Daily Quant

本项目是一个面向 A 股日频研究的本地量化工作台，覆盖三类核心工作：

- 日常更新股票和 ETF 数据
- 运行策略实验与每日推荐
- 执行横截面回测并输出绩效摘要

项目默认使用本地目录保存配置、数据、缓存和结果，适合单机研究、迭代策略和后续接入远端仓库。

## Scope

这是一个本地优先的 A 股日频研究系统，不是实盘交易系统。

当前覆盖：

- 沪市股票
- 沪市科创板股票
- 深市主板股票
- 场内 ETF

当前流程：

- 数据更新和全量回补
- 数据质量检查与失败重试
- 股票池和数据盘点
- 策略实验与参数扫描
- 横截面回测
- 每日推荐和状态摘要
- 本地 dashboard 浏览入口

当前限制：

- 深市创业板 `300/301` 股票池仍未完整接入
- 深市部分遗漏股票仍依赖后续数据源补齐
- `sweep` 在全市场全历史上仍是长任务，适合离线跑
- 不包含分钟级/高频、实盘下单、账户管理、多用户生产服务

## Repository Layout

- `run.py`: CLI 统一入口
- `quant/`: 核心业务逻辑
- `config/`: 配置模板与本地配置
- `data/`: 本地数据、缓存、SQLite 元数据
- `outputs/`: 推荐结果、回测结果、状态摘要
- `docs/`: 仓库文件地图和少量说明文档
- `local_notes/`: 本地研究和验证笔记
- `scripts/`: 日更脚本和定时任务示例

默认 `.gitignore` 会忽略大体量数据与结果：

- `data/daily/`
- `data/raw/`
- `outputs/*.csv`

这意味着仓库更适合提交代码、配置模板和文档，而不提交本地行情数据本身。

## Documentation

- `docs/repository_map.md`: 当前仓库文件地图
- `PROCESS_LOG.md`: 历史工作日志
- `local_notes/system_validation_report.md`: 详细验证记录

## Environment

推荐 Python 3.11+。

安装依赖：

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -r requirements.txt
```

如果当前系统 Python 启用了 PEP 668，无法直接创建或使用虚拟环境，可退回用户级安装：

```bash
python3 -m pip install --user --break-system-packages -r requirements.txt
```

初始化配置：

```bash
cp config/config.example.yml config/config.yml
```

## Common Commands

首次或全量更新股票池后执行：

```bash
python3 run.py update
```

日常快速更新推荐使用：

```bash
python3 run.py fast_update --workers 16 --lookback-days 7
```

日常更新并同步刷新推荐与统计摘要：

```bash
python3 run.py fast_daily --workers 16 --lookback-days 7
```

运行策略实验：

```bash
python3 run.py experiment
```

运行回测：

```bash
python3 run.py backtest
```

生成每日推荐：

```bash
python3 run.py recommend
```

刷新本地状态摘要：

```bash
python3 run.py status
```

生成数据盘点表：

```bash
python3 run.py inventory
```

检查本地日线数据质量：

```bash
python3 run.py quality
```

必要时按统一 OHLCV 规范修复本地日线 CSV：

```bash
python3 run.py quality --repair
```

抽样对比本地最新 K 线和东方财富接口：

```bash
python3 run.py external_check --sample-size 30
```

执行策略参数扫描：

```bash
python3 run.py sweep
```

生成剩余落后数据报告：

```bash
python3 run.py stale
```

重试上一轮更新失败的代码：

```bash
python3 run.py retry_failed --workers 8 --lookback-days 14
```

生成 dashboard：

```bash
python3 run.py dashboard
```

启动本地 dashboard：

```bash
python3 run.py dashboard --serve --host 127.0.0.1 --port 8000
```

启动可远程访问的 dashboard：

```bash
python3 run.py dashboard --serve --public
```

后台启动 dashboard：

```bash
bash scripts/start_dashboard.sh
```

后台启动公网临时访问入口：

```bash
bash scripts/start_public_dashboard.sh
```

说明：

- 脚本会优先使用 `cloudflared quick tunnel`
- 首次运行若本地没有 `tools/cloudflared`，会自动下载
- 会输出一个新的 `https://*.trycloudflare.com/index.html?token=...` 地址
- 这是临时公网地址，重启后通常会变化

关闭公网临时访问入口：

```bash
bash scripts/stop_public_dashboard.sh
```

回补股票长历史：

```bash
python3 run.py backfill_stocks --start-date 2000-01-01 --max-symbols 0
```

回补 ETF 长历史：

```bash
python3 run.py backfill_etfs --start-date 2000-01-01 --max-symbols 0
```

## Key Outputs

每日推荐与状态：

- `outputs/daily_recommendations.csv`
- `outputs/holdings_review.csv`
- `outputs/daily_status.md`
- `outputs/fast_update_failures.csv`

数据盘点：

- `outputs/data_inventory_summary.csv`
- `outputs/data_inventory_detail.csv`
- `outputs/stale_symbols.csv`
- `outputs/stale_summary.csv`

回测结果：

- `outputs/backtest_returns.csv`
- `outputs/backtest_picks.csv`
- `outputs/backtest_summary.csv`
- `outputs/backtest_monthly_returns.csv`
- `outputs/backtest_yearly_returns.csv`
- `outputs/backtest_drawdowns.csv`
- `outputs/backtest_turnover.csv`
- `outputs/backtest_holding_counts.csv`
- `outputs/backtest_validation_buy_hold.csv`

数据质量：

- `outputs/data_quality_summary.csv`
- `outputs/data_quality_report.csv`
- `outputs/external_price_check_summary.csv`
- `outputs/external_price_check.csv`

参数扫描：

- `outputs/strategy_sweep.csv`

公开策略复现：

- `outputs/replication_510300_ma60_public.csv`
- `outputs/replication_dual_ma_internal.csv`
- `outputs/replication_etf_regression_momentum_summary.csv`
- `outputs/replication_etf_regression_momentum_returns.csv`
- `outputs/replication_etf_regression_momentum_picks.csv`

## Validation Workflow

建议在设计新策略前先跑以下验证：

```bash
python3 run.py quality
python3 run.py external_check --sample-size 30
python3 scripts/validate_backtest_basics.py
python3 research/replication/replicate_dual_ma_internal.py
python3 research/replication/replicate_etf_regression_momentum.py
python3 research/replication/replicate_510300_ma60_public.py
```

说明：

- `validate_backtest_basics.py` 用 `510300`、`510050`、`159915` 买入持有验证回测引擎逐日收益与独立收盘价计算完全一致。
- `replicate_dual_ma_internal.py` 用双均线策略验证策略模块输出与独立向量化实现完全一致。
- `replicate_etf_regression_momentum.py` 复现一个公开 ETF 轮动规则：`518880`、`513100`、`159915`、`510300` 中选近 25 日对数价格回归动量最高的 ETF，并验证回测引擎与独立计算完全一致。
- `replicate_510300_ma60_public.py` 记录一个公开 60 日均线策略案例。当前本地前复权口径尚未对齐公开材料收益，保留为后续数据口径和交易假设排查样本。
- `run.py external_check` 会随机抽样本地最新 K 线并请求东方财富同日 K 线，检查 OHLCV 和涨跌幅口径是否一致。

网页入口：

- `outputs/dashboard/index.html`
- `http://<server-ip>:8765/index.html?token=<your-token>`

## Strategy

当前默认策略是一个日频趋势动量策略，基础筛选逻辑包括：

- 股票和 ETF 分别设置最低价格门槛
- 股票和 ETF 分别设置最近 20 日平均成交额门槛
- 使用 `ret_20`、`ret_60`、`ret_120` 做多周期动量筛选
- 使用 20/60 日均线趋势、20 日波动和 20 日回撤做约束
- 使用长期动量 + 趋势 + 低波动组合打分，并截取前 `N` 个标的

参数已配置化，可通过 `config/config.yml` 的 `strategy` 和 `tuning` 段调整。

已注册策略包括：

- `momentum`
- `mean_reversion`
- `low_volatility`
- `dual_ma`
- `buy_and_hold`
- `etf_regression_momentum`

## Backtest

回测模块当前采用日频横截面等权组合：

- 每个交易日根据策略排名选出组合
- 使用下一交易日收益作为前瞻收益
- 成本按实际调仓比例计提，不再按每日满仓换手粗暴扣费
- 输出净值、累计收益、平均换手、胜率、最大回撤等摘要

目前回测框架可用，但策略本身仍需继续优化。

## Daily Operations

推荐使用脚本：

```bash
bash scripts/daily_run.sh
```

安装工作日定时任务：

```bash
bash scripts/install_daily_cron.sh
```

移除定时任务：

```bash
bash scripts/remove_daily_cron.sh
```

脚本当前会执行：

- `python3 run.py fast_daily --workers 16 --lookback-days 7`

并把日志写入：

- `outputs/logs/daily_YYYY-MM-DD.log`

如果当日有未更新成功的代码，可直接查看：

- `outputs/fast_update_failures.csv`
- `outputs/daily_status.md` 的 `Update Failures` 段

如果要看本地网页总览：

- 打开 `outputs/dashboard/index.html`
- 或执行 `python3 run.py dashboard --serve --port 8000`

如果要从其他机器访问：

- 执行 `bash scripts/start_dashboard.sh`
- 使用 `config/config.yml` 里的 `dashboard.port` 和 `dashboard.access_token`
- 访问 `http://<服务器IP>:<端口>/index.html?token=<token>`

## Roadmap

下一阶段建议优先做：

1. 补齐深市创业板与深市缺失股票池
2. 按行业、波动、回撤约束增强策略
3. 扩展回测报告，加入年度分解、分层表现和调仓统计
4. 为参数扫描增加缓存和分批运行能力
