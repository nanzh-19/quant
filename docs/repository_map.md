# Repository Map

日期：2026-07-30

这份文档把仓库按“代码 / 研究 / 脚本 / 本地产物 / 环境缓存”分开，方便快速定位文件用途。

## 1. 顶层结构

```text
.
├── run.py
├── README.md
├── requirements.txt
├── config/
├── data/
├── docs/
├── local_notes/
├── outputs/
├── quant/
├── research/
├── scripts/
├── tools/
├── hongli/
└── .venv / .agents / .codex / __pycache__
```

## 2. 代码主干

### `run.py`

统一 CLI 入口。负责把命令分发到数据更新、回测、推荐、质量检查、dashboard、研究输出等流程。

主要命令包括：

- `update`
- `fast_update`
- `fast_daily`
- `update_sz`
- `experiment`
- `backtest`
- `recommend`
- `daily`
- `status`
- `plot`
- `inventory`
- `quality`
- `external_check`
- `backfill_stocks`
- `backfill_etfs`
- `sweep`
- `stale`
- `retry_failed`
- `repair_recent`
- `dashboard`

### `quant/`

主实现都在这里。当前结构是“核心实现 + 兼容转发层”。

#### 实际实现

- `quant/services/app.py`: 应用上下文、数据拉取、回测、推荐、盘点等编排逻辑
- `quant/engine/backtest.py`: 回测引擎
- `quant/engine/strategy.py`: 策略定义和注册
- `quant/engine/indicators.py`: 指标计算
- `quant/engine/reporting.py`: 回测和诊断报告
- `quant/data/provider.py`: 行情/股票池数据源
- `quant/data/storage.py`: 本地存储读写
- `quant/data/quality.py`: 数据质量检查
- `quant/data/inventory.py`: 数据盘点
- `quant/data/external_check.py`: 外部行情抽样对比
- `quant/data/data_quality.py`: 质量规则补充
- `quant/core/config.py`: 配置模型
- `quant/ui/charting.py`: 图表渲染
- `quant/ui/dashboard.py`: dashboard 页面和服务端
- `quant/research.py`: 研究辅助逻辑

#### 兼容转发

以下顶层模块主要是旧导入路径的转发层，避免已有脚本失效：

- `quant/app.py`
- `quant/backtest.py`
- `quant/charting.py`
- `quant/config.py`
- `quant/dashboard.py`
- `quant/data_provider.py`
- `quant/data_quality.py`
- `quant/external_check.py`
- `quant/indicators.py`
- `quant/inventory.py`
- `quant/quality.py`
- `quant/reporting.py`
- `quant/storage.py`
- `quant/strategy.py`

### `scripts/`

日常运维、验证和实验脚本。

#### 数据与质量

- `scripts/daily_run.sh`
- `scripts/install_daily_cron.sh`
- `scripts/remove_daily_cron.sh`
- `scripts/repair_daily_quality.py`

#### 回测与验证

- `scripts/validate_backtest_basics.py`
- `scripts/validate_backtest_engine_synthetic.py`
- `scripts/validate_stock_strategy_basics.py`
- `scripts/verify_backtest.py`
- `scripts/verify_backtrader.py`
- `scripts/compare_strategies.py`

#### dashboard

- `scripts/start_dashboard.sh`
- `scripts/stop_dashboard.sh`
- `scripts/start_public_dashboard.sh`
- `scripts/stop_public_dashboard.sh`

#### 其他

- `scripts/dividend_analysis.py`
- `scripts/cron_example.txt`

### `research/replication/`

公开策略复现区，用来验证数据、信号和回测口径。

当前文件主要覆盖：

- 股票买入持有
- 股票均线交叉
- GEM
- 固定权重组合
- ETF 双动量
- ETF 回归动量
- RSRS
- 次方量化相关 ETF 策略

### `research/experiments/nasdaq_sp500/`

纳指 / 标普 ETF 的内部研究脚本。

当前脚本包括：

- `generate_nasdaq_sp500_weekly_signals.py`
- `research_nasdaq_sp500_final_validation.py`
- `research_nasdaq_sp500_grid_strategy.py`
- `research_nasdaq_sp500_dual_speed_grid.py`
- `research_nasdaq_sp500_more_strategies.py`
- `research_nasdaq_sp500_other_strategies.py`
- `research_nasdaq_sp500_offensive_strategies.py`

说明：

- 这个目录当前在 git 里还是未跟踪状态。
- 但它已经是仓库研究主线的一部分，建议后续纳入版本控制。

## 3. 配置与说明

### `config/`

- `config/config.example.yml`: 配置模板
- `config/config.yml`: 本地实际配置，按 `.gitignore` 规则不纳入版本控制

### 根目录文档

- `README.md`: 项目说明和常用命令
- `requirements.txt`: Python 依赖清单
- `PROCESS_LOG.md`: 主工作日志，当前记录到 2026-04-24
- `local_notes/system_validation_report.md`: 本地验证记录
- `docs/repository_map.md`: 本文件

### `local_notes/`

本地研究笔记和验证报告，属于工作区材料，不建议推远端。

常见文件：

- `system_validation_report.md`
- `public_strategy_search_report.md`
- `replication_gap_analysis.md`
- `current_etf_portfolio_strategy_research.md`
- `etf_tactical_overlay_research.md`
- `nasdaq_sp500_cap_strategy_research.md`

## 4. 数据与产物

### `data/`

本地历史数据、元数据和缓存目录。当前按 `.gitignore` 规则只保留 `data/.gitkeep` 在仓库里。

### `outputs/`

运行产物目录，包含：

- 推荐结果
- 回测结果
- dashboard 输出
- 数据盘点
- 质量报告
- 日志
- 研究输出

这个目录也是按 `.gitignore` 规则保留空目录，不建议提交大量产物。

### `tools/`

本地辅助工具目录。当前常见用途是放第三方二进制工具或临时依赖。

### `hongli/`

历史专项工作目录，和当前主线量化研究分开。

## 5. 环境和缓存

以下目录不是业务代码的一部分：

- `.venv/`: Python 虚拟环境
- `__pycache__/`: 字节码缓存
- `.agents/`: 代理运行痕迹
- `.codex`: 本地工作状态文件

## 6. 建议的整理原则

1. 代码只放 `quant/`、`scripts/`、`research/`。
2. 运行结果只放 `outputs/`。
3. 本地研究说明只放 `local_notes/`。
4. 配置模板保留在 `config/`，真实配置不入库。
5. `research/experiments/` 如果继续使用，建议补一个 `README.md` 或直接纳入版本控制。
