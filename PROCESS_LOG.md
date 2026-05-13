# 工作日志

## 2026-03-18

### 目标

- 初始化本地 A 股日频量化项目
- 打通数据更新、策略实验、策略回测三条主链路
- 建立基础目录结构和命令入口

### 处理

- 初始化项目骨架，建立：
  - `quant/`
  - `config/`
  - `data/`
  - `outputs/`
- 实现统一命令入口 `run.py`
- 初版数据存储采用：
  - `data/daily/*.csv` 保存单标的日线
  - `data/universe.csv` 保存股票池
  - `data/metadata.sqlite3` 保存更新日志和状态
- 初版行情源采用东财列表接口 + 东财/腾讯历史 K 线
- 修复早期关键问题：
  - 回测前视偏差，改为使用次日收益
  - 增量落盘时 `date` 字段字符串和时间类型混排
- 扩展为“股票 + 场内 ETF”双资产框架：
  - `holdings.csv` 增加 `asset_type`
  - `universe` 增加 `asset_type`、`market_group`
  - 策略按股票和 ETF 使用不同价格/成交额门槛
  - 股票和 ETF 使用不同最小历史样本要求
- 增加命令：
  - `python3 run.py daily`
  - `bash scripts/daily_run.sh`
- 改用交易所官方接口构建股票池和 ETF 池，减少对单一第三方接口依赖
- 为应对深交所接口抖动，增加股票池缓存和失败回退：
  - 新增 `data/raw/universe_cache/`
  - 支持按来源缓存
  - 支持抓取部分成功后回写缓存

### 结果

- 项目具备基础可运行形态
- 推荐、实验、回测三条命令可跑
- 已产出：
  - `outputs/latest_ranking.csv`
  - `outputs/daily_recommendations.csv`
  - `outputs/holdings_review.csv`
  - `outputs/backtest_returns.csv`
  - `outputs/backtest_picks.csv`

### 待办

- 深交所股票池稳定续传
- 更完整的持仓日报
- 更成熟的风控和仓位管理

## 2026-03-22

### 目标

- 针对深圳股票和图形检查补专用能力

### 处理

- 增加深圳股票专项补数入口：
  - `python3 run.py update_sz --max-symbols 200`
- 增加本地 K 线图模块：
  - `python3 run.py plot --symbol 510300 --days 120`
- 输出图片目录：
  - `outputs/charts/`

### 结果

- 可以优先补深圳股票，避免被上海股票和 ETF 挤掉配额
- 可以直接用图形检查 OHLCV 数据是否合理

### 待办

- 继续扩大深圳市场覆盖
- 为策略设计提供更多可视化检查手段

## 2026-04-22

### 目标

- 把项目整理成可持续维护、可放远端仓库的版本
- 提升日更速度与稳定性
- 补上数据盘点、失败追踪、参数扫描和第一轮策略优化
- 增加本地浏览入口

### 处理

- 补齐远端仓库需要的文档：
  - 新增 `REQUIREMENTS.md`
  - 重写 `README.md`
- 增加回测摘要输出：
  - `outputs/backtest_summary.csv`
- 增加策略参数扫描入口：
  - `python3 run.py sweep`
- 将策略参数配置化，支持从 `config/config.yml` 调整
- 增加快速增量更新模式：
  - `python3 run.py fast_update --workers 16 --lookback-days 7`
  - `python3 run.py fast_daily --workers 16 --lookback-days 7`
- 修复快速更新关键稳定性问题：
  - 修复 CSV 末行日期识别错误
  - 写文件改为原子替换，避免半写文件
  - 读取层对空/坏 CSV 增加容错
  - 并发抓取改为线程独立 session
  - 增加进度输出
- 增加失败和落后数据诊断能力：
  - `outputs/fast_update_failures.csv`
  - `python3 run.py stale`
  - `outputs/stale_symbols.csv`
  - `outputs/stale_summary.csv`
  - `python3 run.py retry_failed --workers 8 --lookback-days 14`
  - `outputs/retry_failed_failures.csv`
- 完成一轮大规模快速补齐，ETF 样本 `159915`、`510300`、`510050` 推进到最新交易日
- 对默认策略做第一轮升级，不再只看 `ret_20` / `ret_60`
- 新增策略因子：
  - `ret_120`
  - 20/60/120 日均线
  - `ma_gap_20_60`
  - `volatility_20`
  - `drawdown_20`
- 新策略加入：
  - 趋势过滤
  - 波动过滤
  - 回撤过滤
  - 长中短周期动量组合评分
- 刷新：
  - `outputs/latest_ranking.csv`
  - `outputs/backtest_summary.csv`
- 增加本地 dashboard 入口：
  - `python3 run.py dashboard`
  - 输出 `outputs/dashboard/index.html`
- dashboard 汇总展示：
  - 数据覆盖摘要
  - 更新日志
  - 失败代码
  - 推荐结果
  - 回测摘要
  - 参数扫描结果

### 结果

- 项目文档已达到可远端托管水位
- 快速日更链路比原始全量增量方式更稳、更可追踪
- 数据补齐能力明显增强
- 默认策略从“纯短中期动量”升级为“趋势 + 动量 + 波动/回撤约束”
- 新策略回测结果较旧版本略有改善，但仍为负 Sharpe，尚未达到可用策略标准
- 本地 dashboard 可作为日常第一查看页面

### 待办

- 继续处理数据源 `empty_result` 的少量失败代码
- 对策略做第二轮结构升级
- 参数扫描增加缓存和分批运行能力

## 2026-04-24

### 目标

- 把 dashboard 从“本地页面”提升为“可远程访问”的服务入口
- 确认其他设备可访问，定位网络问题边界

### 处理

- 将 dashboard 改为可远程访问：
  - 服务支持 `0.0.0.0` 监听
  - 增加 token 访问保护
  - 配置写入 `config/config.yml` 的 `dashboard` 段
- 新增 dashboard 管理脚本：
  - `bash scripts/start_dashboard.sh`
  - `bash scripts/stop_dashboard.sh`
- 修复后台启动方式，改用脱离会话方式常驻
- 实测确认：
  - 服务监听在 `0.0.0.0:8765`
  - 本机访问 `127.0.0.1:8765` 返回 `200`
  - 局域网地址 `192.168.51.19:8765` 返回 `200`
- 判断结果：
  - 如果其他电脑打不开局域网地址，问题在网络路径或端口策略，不在应用代码
- 额外临时搭建了一个外网 SSH 隧道访问入口，用于绕过局域网限制
- 将临时外网访问整理为脚本：
  - `bash scripts/start_public_dashboard.sh`
  - `bash scripts/stop_public_dashboard.sh`
  - 自动读取 `config/config.yml` 的 `dashboard.port` 和 `dashboard.access_token`
  - 自动输出新的公网访问 URL
- 将公网临时入口从 `localhost.run` 调整为 `cloudflared quick tunnel`
  - 避免跳转到第三方管理登录页
  - 实测返回 `trycloudflare.com` 地址可直接访问 dashboard 页面

### 结果

- dashboard 已具备局域网远程访问能力
- 项目已具备“脚本启动 + token 保护 + URL 输出”的基本服务化形态
- 已验证代码、服务和本机访问链路正常
- 网络不可达问题已收敛到服务器外部网络环境，而不是应用本身
- 公网临时访问入口已具备脚本化重建能力，旧链接失效后可重新生成新链接
- 公网临时访问入口切换为 `trycloudflare.com`，比原先 `localhost.run` 更适合当前这个场景

### 待办

- 把 dashboard 扩展为真正的数据浏览器：
  - 股票/ETF 数量统计
  - 标的列表
  - 单标详情页
  - 可拖动缩放的 K 线图

## 2026-04-24 — Claude Code: 策略模块重构与回测验证

### 目标

- 重构策略模块，建立可扩展的策略接口体系
- 修复回测引擎的调仓频率问题（原来每天全量调仓，换手率 ~50%/天，交易成本吃掉所有收益）
- 验证回测引擎的正确性

### 处理

- 策略模块重构（`quant/strategy.py`）：
  - 定义 `BaseStrategy` 抽象基类（ABC），统一接口：`name`、`params()`、`rank()`、`review_holdings()`
  - `MomentumStrategy` 继承 `BaseStrategy`，逻辑不变
  - 新增 `BuyAndHoldStrategy`：买入指定标的并一直持有，用于回测引擎验证
  - 新增 `STRATEGY_REGISTRY` 策略注册表 + `create_strategy()` 工厂函数
  - 后续新增策略只需继承 `BaseStrategy` 并注册即可
- 回测引擎修复（`quant/backtest.py`）：
  - 新增 `_is_rebalance_day()` 函数，支持 daily/weekly/monthly 三种调仓频率
  - weekly 判断逻辑：weekday 变小或间隔 >= 3 天（处理节假日）
  - monthly 判断逻辑：月份或年份变化
  - 非调仓日沿用上期持仓，只计算当日收益，不产生交易成本
  - 修复 turnover 变量作用域问题
- 应用入口更新（`quant/app.py`）：
  - 策略创建改用 `create_strategy()` 工厂函数，通过 config 中的 `strategy.name` 查找策略类
- 配置更新（`config/config.yml`）：
  - `backtest` 段新增 `rebalance_frequency: weekly`
- 回测正确性验证：
  - 新增 `scripts/verify_backtest.py`：用 510300 买入持有对比手动计算
    - 累计收益差异: 0.000000
    - 年化收益差异: 0.000000
    - 波动率差异: 0.000082
    - Sharpe 差异: 0.000018
    - 最大回撤差异: 0.000000
    - 结论：完全一致
  - 新增 `scripts/verify_backtrader.py`：用 backtrader 交叉验证
    - 对齐数据区间后，买入持有累计收益差异: 0.002592（0.26%）
    - 差异来源：backtrader 持有到最后一天收盘 vs 自研引擎用 fwd_ret_1 持有到次日收盘
    - 结论：在合理误差范围内，引擎正确
- 依赖更新（`requirements.txt`）：新增 backtrader

### 结果

- 策略模块具备可扩展架构，后续可方便地插入均值回归、多因子等新策略
- 回测引擎调仓频率问题已修复，默认周度调仓，换手率将大幅降低
- 回测引擎正确性已通过两种方式验证：
  1. 买入持有手动计算对比：所有指标差异 < 0.0001
  2. backtrader 交叉验证：累计收益差异 < 0.3%

### 待办

- 多因子组合策略（动量 + 价值 + 质量 + 低波动）
- 因子标准化（z-score）和行业中性化
- 止损/止盈机制
- 仓位管理（凯利公式或风险平价）
- 过滤 ST 股和次新股

## 2026-04-24 — Claude Code: 新增策略 + 多策略回测对比 + 报告

### 目标

- 新增 3 个经典策略，验证策略框架的可扩展性
- 用自研引擎跑多策略回测对比
- 用 backtrader 交叉验证策略逻辑方向性
- 生成可读的策略回测报告

### 处理

- 新增 3 个策略（`quant/strategy.py`）：
  - `MeanReversionStrategy`（均值回归）：选近 20 日超跌但 60 日趋势尚可的标的
  - `LowVolatilityStrategy`（低波动）：选波动率最低的标的，利用低波异象
  - `DualMAStrategy`（双均线趋势）：MA20 > MA60 且价格在 MA20 之上时买入
  - 提取公共 `_liquidity_filter()` 函数，统一流动性过滤逻辑
  - 全部注册到 `STRATEGY_REGISTRY`
- 编写多策略回测对比脚本（`scripts/compare_strategies.py`）：
  - 自研引擎：4 种策略在 4723 个标的上跑 2018-2025 周度调仓回测
  - backtrader：4 种对应策略在 510300 单标的上跑交叉验证
  - 修复 backtrader 仓位计算问题（满仓买入时佣金导致 Margin 拒单，改为 95% 仓位）
  - 结果输出到 `outputs/strategy_comparison.json`
- 生成策略回测报告（`outputs/strategy_report.html`）：
  - 第一部分：4 种策略的通俗说明
  - 第二部分：自研引擎回测结果表格 + 解读
  - 第三部分：backtrader 交叉验证结果 + 对比分析
  - 第四部分：引擎正确性验证详情
  - 第五部分：结论与下一步
- 修正 PROCESS_LOG 标题格式：用"模型名: 做了什么"替代"(2)"编号

### 结果

- 自研引擎 4 种策略回测结果（2018-2025，周度调仓）：
  - 低波动策略：累计 -0.03%，Sharpe -0.000，最大回撤 -37.72%（最好）
  - 均值回归策略：累计 -71.56%，Sharpe -0.442
  - 动量策略：累计 -98.51%，Sharpe -0.981
  - 双均线策略：累计 -99.90%，Sharpe -1.314（最差）
- backtrader 交叉验证（510300 单标的）：
  - 买入持有 +14.08%，均值回归(RSI) +9.02%，动量(ROC) +3.07%，双均线 -12.92%
  - 策略相对排序与自研引擎一致，验证策略逻辑方向性正确
- 结论：引擎可信，策略框架可扩展，但简单单因子选股在 A 股效果不佳

### 待办

- 多因子组合策略（动量 + 价值 + 质量 + 低波动）
- 因子标准化（z-score）和行业中性化
- 止损/止盈机制
- 过滤 ST 股和次新股
