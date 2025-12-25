# Address Tagging

an address system which can automatically tag addresses based on their trading behavior and other on-chain activities.

## Questions

- how to find the relation between multi addresses?

## Planing

计算地址协同性:
  对每一对地址 (A, B)，计算其在滑动窗口内的：
    时间同步性：交易时间差的分布（如 90% 的交易在 ±500ms 内）
    方向一致性：同向交易占比（如 A 买 → B 买）
    价格协同性：交易价格接近程度（如都在 mid ± 0.1%）
    量级比例稳定性：size_A / size_B 是否稳定（如固定 2:1）
基于地址活跃时间重叠:
  多地址几乎同时开始活跃/停止活跃
  特征：Jaccard(active_window_A, active_window_B)
构建"地址簇"
  方法：
    图聚类（Graph Clustering）：节点 = 地址, 边权重 = link_score, 算法：Louvain / Leiden（高效，适合大规模）
    规则聚类（轻量版）：若 A 与 B 关联，且 B 与 C 关联 → 将 A/B/C 归为同一簇（传递闭包）
  输出：cluster_id → [addr1, addr2, ...]
对"地址簇"打标
  单地址行为, 簇行为推断, 标签示例
  多地址同步反向套利, CEX 事件搬运者, cex_arb_fleet
  多地址轮动建仓, 机构分仓, institution_cluster
  高频小单、方向一致, 量化策略分片, quant_strategy_X
用"簇"代替"地址"进行行为预测
  输入特征
    cluster_net_vol = Σ(addr.net_vol)
    cluster_gross_vol = Σ(addr.gross_vol)
    intra_cluster_delay_std：簇内交易时间差标准差（越小越协同）
    cross_addr_size_corr：地址间 size 的相关性
  预测目标
    预测 整个簇 在下一窗口是否行动、方向、强度
    因为策略由簇执行，单个地址行为是噪声

## Address Label Examples

| Tag Name          | Description            | Trading Behavior Insights                   |
| ----------------- | ---------------------- | ------------------------------------------- |
| whale_trader      | 大单、方向性强、低频   | 其开多 ≈ 强看涨情绪；反向可能预示反转       |
| retail_chaser     | 小单、追涨杀跌、高频率 | 情绪滞后，其疯狂买入 ≈ 情绪高点（反向信号） |
| market_maker      | 双向挂单、提供流动性   | 情绪中性，但撤单行为可预示波动加剧          |
| arbitrage_bot     | 无方向、套利、快进快出 | 不贡献情绪，可忽略或用于过滤噪音            |
| degen_solo_trader | 高杠杆、频繁爆仓       | 极端情绪放大器（可单独建模）                |

## Tasks

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
