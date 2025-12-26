
## ✅ 目标重述

- **输入**（每个样本）：
  - 某地址 `A` 在过去 `T` 分钟内的：
    - `trades`: (time, side, size, price)
- **输出**：
  - 未来多个时间尺度（5min, 1h, ..., 14d）的 **价格概率分布**
    - 推荐建模为：**分位数回归（Quantile Regression）**，输出如 P10 / P50 / P90
    - 或：**分类化价格变动区间**（如 [-5%, -2%, 0, +2%, +5%] 的概率）

> 注意：你不是预测“该地址行为”，而是**用该地址行为作为市场信号，预测价格**——这隐含假设：**某些地址（如 smart money）的行为领先于价格**。

---

## 一、整体架构

```text
[Raw Trades] 
   ↓ (按地址 + 滑动窗口聚合)
[Address-Level Features] 
   ↓ (拼接 K线特征)
[Feature Matrix per Address-Window]
   ↓ (多任务分位数回归模型)
[Price Distribution @ Δt ∈ {5m, 1h, ..., 14d}]
```

---

## 二、输入特征工程（核心）

### 1. **地址行为特征（来自 trades + positions）**

对每个地址 `A` 和每个训练时间点 `t`，构建过去 `W = 60 分钟` 的特征（可调）：

| 类别 | 特征示例 |
|------|--------|
| **交易活跃度** | trade_count, total_gross_vol, avg_size, max_size |
| **方向性** | net_vol (buy - sell), net_vol / gross_vol |
| **持仓变化** | Δposition_last_5m, Δposition_last_60m, current_position |
| **行为模式** | first_side, last_side, side_switch_count |
| **与价格互动** | % trades above VWAP, % trades near high/low |

> ✅ **关键**：所有特征必须**可实时计算**（你用 `asyncpg` 从 PG 读取 trades 即可推算）

---

### 2. **市场背景特征（K线）**

必须加入！否则模型无法区分“地址 A 买入”是在牛市还是熊市。

对每个 `t`，拼接多个时间尺度的 K线特征：
- **基础**：当前 1m / 5m / 15m K线（OHLCV）
- **技术指标**：
  - RSI(14), MACD, Bollinger Band %B
  - Volatility (std of returns over 1h)
  - Price vs 20EMA, 50EMA

> 你已有 K线数据，这部分可复用。

---

### 3. **地址身份特征（可选但推荐）**

- `is_whale`: size > 95% quantile
- `historical_pnl_sharpe`: （若可估算）
- `tag`: 如 "smart_money"（即使粗标签也有用）

> 即使未打标，可用 `historical_net_vol_consistency` 代理 smartness。

---

## 三、输出：多尺度价格概率分布建模

### 💡 推荐方案：**多任务分位数回归（Multi-Task Quantile Regression）**

- 对每个预测窗口 `Δt ∈ {5m, 1h, 4h, 12h, 1d, 7d, 14d}`：
  - 预测 **3 个分位数**：`q=0.1, 0.5, 0.9`
  - 损失函数：**Pinball Loss**
    ```python
    def pinball_loss(y_true, y_pred, q):
        e = y_true - y_pred
        return torch.max(q * e, (q - 1) * e)
    ```

- **优点**：
  - 直接输出概率分布形状（可计算 VaR、预期收益等）
  - 比分类更精细，比分位数更鲁棒

### 替代方案：**分类化价格变动**

- 将未来收益率 `r = (p_future - p_now) / p_now` 离散化为 11 bins：
  ```text
  [-10%, -5%, -2%, -1%, -0.5%, 0, +0.5%, +1%, +2%, +5%, +10%]
  ```
- 输出每个 bin 的概率（softmax）
- 优点：训练稳定；缺点：损失尾部信息

> **建议先用分位数回归**，更符合“概率分布”需求。

---

## 四、模型结构建议

### 轻量级（推荐，满足 1h 闭环）：
- **输入维度**：~50（地址特征）+ ~30（K线特征）= 80
- **模型**：**MLP + 多任务头**
  ```python
  shared = MLP([80 → 128 → 64])
  head_5m = QuantileHead(64, n_quantiles=3)
  head_1h = QuantileHead(64, n_quantiles=3)
  ...
  ```
- **训练**：PyTorch（支持多输出）

### 进阶（若需捕捉时序）：
- **Temporal Fusion Transformer (TFT)**：天然支持多尺度预测
- **但**：训练慢，推理复杂 → **暂不推荐**

---

## 五、标签（Target）构造

对每个训练样本（时间 `t`）：

1. 获取当前价格 `p_t`（如 1m K线 close）
2. 对每个 `Δt`：
   - 获取 `p_{t+Δt}`（注意：用 **下一个完整 K线的 open**，避免未来函数！）
   - 计算收益率 `r = (p_{t+Δt} - p_t) / p_t`
   - 或直接预测 `p_{t+Δt}`（但收益率更平稳）

> ⚠️ **关键**：确保 `t+Δt` 的价格在训练时**不可见**，否则数据泄露！

---

## 六、训练数据构建（PostgreSQL + Python）

```python
# 伪代码：生成训练样本
async def build_training_samples(conn, coin="SOL", window_past=3600, window_future=300):
    # 1. 获取所有 trades & K线时间对齐
    trades = await conn.fetch("SELECT ... WHERE coin=$1 ORDER BY time", coin)
    klines = await conn.fetch("SELECT time, close FROM klines_1m WHERE ...")

    # 2. 按 5min 步长滑动
    for t in range(start_time, end_time, 300_000):  # 5min = 300s = 300000ms
        # 提取 [t - window_past, t) 的 trades for all addresses
        recent_trades = [tr for tr in trades if t - window_past*1000 <= tr.time < t]
        
        # 按地址聚合
        for addr, group in groupby(recent_trades, key=lambda x: x.taker):
            addr_features = extract_address_features(group)
            market_features = extract_kline_features(klines, t)
            features = addr_features + market_features

            # 构造多尺度 target
            targets = {}
            for delta in [300, 3600, 14400, ...]:  # seconds
                future_price = get_price_at(klines, t + delta*1000)
                if future_price:
                    targets[f"return_{delta}s"] = (future_price - current_price) / current_price

            yield features, targets
```

---

## 七、验证与评估

### 1. **回测设计**
- **时间序列分割**：训练集 = [T0, T1]，验证集 = [T1, T2]，测试集 = [T2, T3]
- **避免随机打乱**！

### 2. **评估指标**
| 目标 | 指标 |
|------|------|
| 分位数准确性 | Pinball Loss @ q=0.1/0.5/0.9 |
| 方向预测 | Accuracy of sign(return_pred_median) |
| 尾部覆盖 | % 真实价格落在 [P10, P90] 区间内（应 ≈80%） |

### 3. **实际 trades 验证**
- 检查：当模型预测“未来 1h P90 >> P10”（即大涨概率高）时，
  - 是否有 `smart_money` 地址在 `t` 时刻净买入？
  - 他们的后续 trades 是否盈利？

---

## 八、部署与你的系统集成

1. **特征生成服务**：
   - 每 5 分钟，用 `asyncpg` 从 PG 读取新 trades
   - 为活跃地址生成最新特征向量

2. **模型推理**：
   - 输入：特征向量 → 输出：7 个时间尺度的价格分位数
   - 推理时间 < 50ms（MLP 足够快）

3. **交易决策**：
   - 若 `P50(1h) > current_price * 1.01` 且 `P90 - P10 > 0.02` → 开多
   - 结合你的 **5 倍杠杆 + 1% 日收益目标**

