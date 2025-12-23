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

- [ ] Implement trade subscriber
  - 有任何报错需要输出到日志
  - 程序需要稳定运行，不要因为网络问题崩溃、自动重试
- [ ] index address trade history for fast query
  - if data is not enough, fetch historical trades from Hyperliquid REST API.
  - should check the data freshness before fetching.
  - should add last fetch timestamp to avoid duplicate fetching.
  - should merge the fetched data with existing data to avoid duplicates.
- [ ] Implement address tagger

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
