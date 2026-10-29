# A-Quant V1

本地可运行的 A 股量化研究工具：Vue3 + Vite + ECharts、FastAPI、SQLite、可替换的本地行情快照、AKShare 适配器、自研逐日事件回测。

**不连接券商、不自动交易、不对收益做任何承诺。** 默认使用固定日期的合成示例行情，示例行情永久禁止买入。

---

## 1. Windows 启动

需要 Python 3.11+（安装时勾选 `py` 启动器）和 Node.js 20.19+ / 22.12+。首次安装依赖需要联网。

```powershell
powershell -ExecutionPolicy Bypass -File .\start.ps1 -Install
```

浏览器打开 <http://127.0.0.1:8765>，停止按 Ctrl+C。以后启动用 `.\start.ps1`（不加 `-Install`）。

- 没有 `py`：用已安装 Python 的绝对路径执行 `-m venv .venv`，再跑启动脚本。不要双击 `.py` 文件。
- 服务只绑定本机，没有多人认证，**不要开放到公网**。
- 开发方式：后端 `.venv\Scripts\python.exe -m uvicorn backend.app:app --port 8765`；前端在 `frontend` 里 `npm ci`、`npm run dev`。接口文档 <http://127.0.0.1:8765/docs>。

### 双击入口（不用记命令）

| 双击 | 作用 | 等价命令 |
|---|---|---|
| `run-start.cmd` | 启动网页服务（只启动，不装依赖、不同步行情） | `powershell -ExecutionPolicy Bypass -File .\start.ps1` |
| `run-daily.cmd` | 全市场收盘同步 + 生成日报；默认权益 10 万，可带参数 | `powershell -ExecutionPolicy Bypass -File .\daily.ps1 -Capital 100000` |
| `run-review.cmd` | 只读体检/研究工具，默认跑 `snapshot`；可带子命令 | `.venv\Scripts\python.exe -X utf8 -m tools.review` |
| `run-sync.cmd` | 只同步腾讯行情，**不**生成日报 | `.venv\Scripts\python.exe -m backend.cli sync --provider tencent` |

- 权益不是 10 万时：命令行运行 `run-daily.cmd 250000`，或直接改 `daily.ps1` 的参数。
- `run-review.cmd` 也能带子命令：`run-review.cmd study`、`run-review.cmd refine`、`run-review.cmd snapshot --mode demo`。
- **只有第一次**需要 `start.ps1 -Install`（装 Python 依赖 + 构建前端）；之后双击 `run-start.cmd` 即可。若 `.venv` 已删除或 `frontend\dist` 不存在，仍要再跑一次 `-Install`。
- 这些 `.cmd` 末尾都有 `pause`，出错时窗口不会一闪而过，方便看报错。

## 2. 第一次使用：先记账，再看建议

1. 打开「成交与持仓」，登记**已经成交**的代码、方向、数量、价格、日期（可先用 `600001 / 100 股 / 10 元` 试，那不是真实行情）。
2. 买入自动进入未清仓持仓；部分卖出只扣减数量，全部卖出才移除。买入用加权成本，卖出不改剩余均价。盈亏是未计费用的浮动盈亏。
3. 每次日报固定顺序：**全部持仓分析 → 组合风险 → 新股票扫描**。持仓后来变成 ST、流动性不足或不在允许板块，也照样分析或明确报缺失。
4. 账户总权益由你自己填（含现金 + 持仓），不是券商数据。演示账本与正式账本互相隔离。
5. 只接受按日期顺序登记的成交；拒绝超量卖出；同一个 `request_id` 不会重复计入。**不校验你的成交是否符合策略。**

扫描、止损建议、回测、定时任务**都不能修改持仓**；只有 `/api/trades` 的成交报告会追加账本，没有"建议卖出就自动清仓"的路径。

## 3. 每天收盘后怎么用

正式行情需要单独装依赖（会联网）：

```powershell
.venv\Scripts\python.exe -m pip install -r requirements-live.txt
.venv\Scripts\python.exe -m backend.cli sync                          # 全市场同步；首次回补450天，之后只取新增交易日
.venv\Scripts\python.exe -m backend.cli report --mode live --capital 100000
```

或者一条命令：`powershell -ExecutionPolicy Bypass -File .\daily.ps1 -Capital 100000`（同步 + 持仓优先日报）。

生成 `data/report-live.json` 后，页面切到「AKShare 本地缓存」再点「重新分析」。**前端刷新只读缓存，不会偷偷发起几千次行情请求。**

- 东方财富日线连不上时：先 `tests\probe_universe.py` 验证新浪名单，再用 `-m backend.cli daily --provider tencent`（新浪名单 + 腾讯不复权日线）。单只连接成功不代表全市场同步通过。
- 只补某一只股票（局部补取，不要和全市场同步同时跑）：
  `.venv\Scripts\python.exe -X utf8 -m backend.cli repair --provider tencent --code 002714`
- 可选定时任务（**脚本随项目交付，未替你注册**）：`.\install-schedule.ps1 -Capital 100000`，工作日 18:00 运行。需要开机、联网、AKShare，Windows 时区设为中国时间。用「任务计划程序」查看或删除 `A-Quant-V1-Close` 即可停用。

**同步完先跑一次体检**（见第 7 节 `snapshot`）：确认 `complete=true`、`errors=0`、`as_of` 是最近的已收盘交易日，且目标日 K 线覆盖率 ≥98%。覆盖率不足时页面会明确告警并禁止新计划。

## 4. 当前策略规则

股票池：沪深主板 600/601/603/605、000/001/002/003，创业板 300/301；排除科创板、北交所、名称含 ST 或"退"的股票；不限市值。要求至少 60 根有效日线、最新交易日有成交、近 20 日平均成交额 ≥5000 万元，且近 60 根 K 线内没有超过涨跌停幅度的异常跳变。数据缺失从严排除。

- **S1（突破）**：收盘突破前 20 日唐奇安上轨（不含当天）、成交量 / 前 20 日均量 ≥1.3、收盘高于 SMA20。
- **S2（回调）**：SMA20>SMA60 且 SMA20 较前日上行；收盘在 SMA20 上方 0–1 倍 ATR14；RSI14 在 40–60。
- **严格复筛（页面默认开）**：S1 追加 ADX14≥25 且 +DI>−DI、RSI14≤75、距 SMA20≤3ATR；S2 追加 ADX14≥20 且 +DI>−DI、收盘高于前日。
- **振幅过滤（页面默认开）**：信号日振幅超过该模型上限（S1 **2.5**×ATR、S2 **1.5**×ATR）视为追高，不进入候选。**上限必须按模型分开**：放量突破当天天然是大振幅（实测当日 S1 最小 1.77×ATR、中位 2.11），用同一个 1.5 会把 S1 全部剔除（0/12），只剩 S2。
- **策略筛选（默认全部）**：页面「策略」可切到「仅 S1 突破」或「仅 S2 回调」，只影响展示与预算分配，不改判定。面板会显示"命中 S1 x / S2 y 条"。
- **弱市暂停（页面默认开）**：等权指数低于自身 MA20 时候选标为"仅研究"、不分配数量。最后交易日样本不足全市场一半时**自动不拦截**，并在页面标出"样本不足"。
- **每日名额（默认 3，可调 5/10）**：候选按 20 日均成交额降序（**流动性排序，不是预期收益排名**），只有前 3 条进入预算分配，其余进 `candidates_overflow` 仅备查。

ATR / RSI / ADX 用 Wilder 平滑（ATR 从第二根 K 线的真实波幅开始，前 14 个值均值初始化；RSI 涨跌都为 0 时取 50）。参数在 `backend/engine.py` 的 `STRATEGY`，都是**研究默认值，没有样本外收益验证**。

### 止损、仓位与退出

- 新计划止损参考 = 收盘价 − 2×ATR；数量按 1% 权益风险预算、20% 单股上限、70% 组合上限取 100 股整数；计划风险低于权益 0.3% 的不分配（避免凑最小的 100 股）。
- 页面不再展示 2R 预测止盈，只显示**每股风险距离**。
- 持仓没有入场 ATR 和策略归属记录，所以持仓页显示的是"成本 − 2×当前 ATR"**每日重算**的参考线，不是锁定止损、也不是只升不降的追踪止损。触及该线提示评估退出，跌破 SMA20 提示评估减仓，仍需人工复核除权与公告。
- 回测：信号日收盘定 ATR，次日开盘入场（且开盘涨幅 ≤1% 才成交），以成交价 −2×ATR 固定止损；收盘触及止损、跌破 SMA20 或到达持有上限后次日退出。S1 最长 8 个交易日，S2 最长 30 个交易日。

### 研究结论（为什么默认只显示 3 条）

在 2025-08 至 2026-10 的 4,422 只、约 90 万个"池内股票日"上实测（`tools.review refine`）：

- S1 的 10 日超额约 −0.3pp、S2 约 −0.8pp，**两段子样本都为负**——当前规则没有可辨识的正边际，只能当研究基线。
- 加指标大多无效：布林 %B>0.5 保留 100%、MACD>0 保留 99%（已被"突破"本身蕴含）；ADX、量比、KDJ 在剥离择时后为 0 或负。
- 唯一通过两段检验的选股型过滤是"信号日振幅不要太大"（同日 +0.43pp）；但它必须按模型设阈值——1.5×ATR 对 S1 是致命的，见 §4。
- 市场状态过滤只改善"要不要出手"，不改善"选哪只"（同日效应 0.00）。
- 排序比过滤有效：取成交额前 3 的 10 日超额 +0.45pp，而按量比排序 −3.35pp。
- **S2 的所有组合方案仍为负**，问题在退出不在入场，重做退出前建议只做 S1。

## 5. 数据质量与替换

示例行情、缓存过期、盘中未收盘、交易日历不足、全市场不完整、覆盖率不足、任一持仓无法估值：**全部把新增数量置 0**，并在页面写明原因。非交易日与盘中只允许最近一个已完成的收盘交易日，15:30 是保守确认时间。**供应商的日线更新可能晚于 15:30**：覆盖率不足时只会拦住新计划，隔一段时间重跑同步即可补齐，不要据此认定停牌。

`backend/data.py` 是数据边界，引擎只读下面结构的 JSON（金额单位元、成交量单位股、价格同一复权口径、日期升序且唯一、OHLC 合法）：

```json
{"mode":"live","as_of":"2026-10-27","expected_session":"2026-10-27","calendar_verified":true,"calendar":["2026-10-26","2026-10-27","2026-10-28"],"complete":true,"errors":[],"stocks":[{"code":"000001","name":"股票名称","bars":[{"date":"2026-10-27","open":10,"high":11,"low":9,"close":10.5,"volume":10000000,"amount":105000000}]}]}
```

正式缓存是 `data/live.json`。**不要手动把 `calendar_verified` 或 `complete` 设成 true 来绕过校验。** 适配器调用 [AKShare](https://akshare.akfamily.xyz/data/stock/stock_quote.html)（`stock_zh_a_spot_em` / `stock_zh_a_hist` / `tool_trade_date_hist_sina`）。

**已知限制**：正式数据是不复权原始价格（为了不拿复权价和真实成本混用），未自动处理除权、分红、送转；长周期回测有明显限制。免费行情会限流、变更或不可用，V1 不保证获取成功。同步默认增量：只从缓存最后一根 K 线之后开始取，8 路并发（`--workers 1` 恢复串行），没有断点续传；供应商事后修订的历史 K 线不会被增量同步发现，需要 `--full` 整段重取。

## 6. 回测边界

自研单股逐日事件回测：信号日收盘产生信号，次日开盘成交（开盘涨幅 >1% 不追），T+1，100 股整手；停牌不成交，接近涨跌停开盘时保守跳过，条件仍成立则次日重试；末日未平仓按收盘估值单独显示。

费用假设：双边佣金 0.03%（每笔至少 5 元）、卖出税费 0.05%、滑点 0.1%。这些是**模拟参数**，不是对现行或历史法定费率的确认；接口支持 `commission` / `tax` / `slippage` 输入。

没有做：历史全市场成分、ST 变更、退市股票、复权事件、真实成交队列、行业相关性、组合级回测、基准收益、样本外验证。**当前股票池回测存在幸存者偏差，不能用来声称预期收益。**

## 7. 策略评审工具（只读）

`tools/review` 在真实缓存上回答"规则到底有没有边际"，**不联网、不改持仓、不下单**：把引擎的 Wilder 递推每只股票只算一遍（引擎是逐日重算 O(n²)），并用自检保证与 `backend.engine` 逐字段、逐信号一致。

```powershell
.venv\Scripts\python.exe -m tools.review                  # 快照体检（默认，约 18 秒）
.venv\Scripts\python.exe -m tools.review study            # 信号后 1/3/5/8/10/20 日超额、离场归因
.venv\Scripts\python.exe -m tools.review sensitivity      # 唐奇安窗口 × 量比网格
.venv\Scripts\python.exe -m tools.review refine           # 候选过滤 vs 排序实验（约 40 秒）
.venv\Scripts\python.exe -m tools.review backtest         # 真实引擎抽样回测（默认 150 只，约 1 分钟）
```

- `snapshot`：覆盖率、异常跳空、停牌缺口，以及"日报现在会给出什么"（池子大小、命中几条、显示前几条、市场状态、分到哪几只、单笔风险）。**同步后先跑这个。**
- 结果同时打印并写入 `data/review/*.json`（已在 `.gitignore` 中）。
- 参数：`--mode live|demo`、`--capital`、`--stocks N`、`--no-strict`、`--adx`、`--no-positions`。

## 8. 测试与备份

```powershell
.venv\Scripts\python.exe -m unittest discover -s tests -v
cd frontend
npm run build
```

- `data/state.sqlite3` 是你的真实账本，停止工具后复制备份；不要用测试覆盖它（测试用临时数据库）。
- `data/demo.json` 是可复现的合成示例行情。
- 改了前端源码后必须重新 `npm run build`，否则页面还是旧 bundle。

## 9. 目录结构

```
backend/          engine.py（规则与回测）、data.py（数据边界/同步）、
                  quality.py（覆盖率与异常跳变）、store.py（账本）、app.py（API）、cli.py（命令行）
frontend/         src/App.vue + Vite 构建产物 dist/
tools/review/     只读评审工具（panel / quality / study / refine / 命令行）
tests/            单元与集成测试（含 test_review_tooling.py）
data/             live.json（正式缓存）、demo.json（示例）、state.sqlite3（账本）、review/（评审输出）
```
