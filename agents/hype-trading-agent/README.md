# Hypeliquid Trading Agent

1. a trade subscriber:
   subscrube to Hyperliquid's WebSocket API for real-time market data and save the trade data into pg.
2. a address tagger:
   an ai agent which receive last [100, 50, 20] trades of the address and tag the addresses.
   the address tag should represent the trading behavior of the address, e.g., "arbitrage bot", "market maker", "whale trader", etc.
   write the tagged address to address tag history table.
   user can query the address tag history table to get the tag history of an address, the latest tag is the most accurate one.
3. trading agent:
   an ai agent which receive real-time trades and address tags, and make trading decisions based on the data, output the reasoning and the trade action.
4. trade executor:
   use freqtrade as executor, receive the trade action from trading agent and execute the trade on Hyperliquid.
5. analytics dashboard:
   use freqtrade's backtest and dry-run features to evaluate the performance of the trading agent, and display the results on a dashboard.
6. reflection module:
   periodically review the backtest and dry-run results, and update the trading strategy accordingly.

## Tasks

- [x] Implement trade subscriber
  - 有任何报错需要输出到日志
  - 程序需要稳定运行，不要因为网络问题崩溃、自动重试
- [x] index address trade history for fast query
  - if data is not enough, fetch historical trades from Hyperliquid REST API.
  - should check the data freshness before fetching.
  - should add last fetch timestamp to avoid duplicate fetching.
  - should merge the fetched data with existing data to avoid duplicates.
- [ ] a address info collector demo for single python script
   - input: a list of addresses
   - output: full address info including:
     - trade history
     - deposit & withdraw history
     - spot holding (only > $10)
     - open orders
     - Perps Position Value
     - Account Total Value
     - Free Margin Available
     - Positions Table
     - PnL (Profit & Loss)
- [ ] a info fill background task
  - fill address position
  - address deposit & withdraw
  - address spot holding(only > $10)
  - address open orders
- [ ] Implement address tagger
  - 规则初筛 + LLM 复核高价值地址
  - XGBoost 地址行为预测模型
    目标：预测下一个时间窗口（如 1min）内，某地址是否会
    - 开多 / 开空 / 平仓 / 不动
    - 交易方向（side）、预期 size
    输入特征：
    - 地址标签，tag = "smart_money"
    - K线数据，最近 5 根 1min K线：open, high, low, close, volume, RSI, MACD
    - 当前持仓，该地址当前净持仓方向 & size（需从历史 trades 推算）
    - 微观结构，最近 10 笔该地址的 trades（side, size, relative to mid-price）
  - 行为预测 → 市场价格预测
    - 预测每个 trader 行为 → 计算未来价格
  - 模型验证
      验证流程：
      1. 固定时间点 T：用 T 时刻前的数据：给地址打标 + 获取 K线 + 推算持仓
      2. 预测 [T, T+Δt) 内行为
      3. 对比真实 trades：若地址在 [T, T+Δt) 有 trade → 检查预测方向/是否交易 是否正确
      4. 评估指标：
        - 方向准确率（Direction Accuracy）
        - 行为召回率（Recall of active traders）
        - size 预测 MAE
- [ ] decesion making agent
- [ ] freqtrade integration
- [ ] analytics dashboard
- [ ] reflection module

## Address Label Examples

| Tag Name          | Description            | Trading Behavior Insights                   |
| ----------------- | ---------------------- | ------------------------------------------- |
| whale_trader      | 大单、方向性强、低频   | 其开多 ≈ 强看涨情绪；反向可能预示反转       |
| retail_chaser     | 小单、追涨杀跌、高频率 | 情绪滞后，其疯狂买入 ≈ 情绪高点（反向信号） |
| market_maker      | 双向挂单、提供流动性   | 情绪中性，但撤单行为可预示波动加剧          |
| arbitrage_bot     | 无方向、套利、快进快出 | 不贡献情绪，可忽略或用于过滤噪音            |
| degen_solo_trader | 高杠杆、频繁爆仓       | 极端情绪放大器（可单独建模）                |

> 为每类标签定义 情绪权重（sentiment weight） 和 方向系数（+1 多 / -1 空）

## AI Trade Action

actions:

- create_order
- cancel_order
- hold_position
- reduce_position
- increase_position
- close_position
- stop_loss
- take_profit

side: buy/sell
size: in usdt with 5x leverage
symbol: sol/usdt, eth/usdt, btc/usdt
price: market price
reasoning: the reasoning behind the action

## Target

- must have an operator within 1hour. the operator can be create order/cancel order/order completed.
- the profit target is 3% daily return, can create 5x leverage.
- start from $100 of capital, symbol: [sol, eth, btc].
