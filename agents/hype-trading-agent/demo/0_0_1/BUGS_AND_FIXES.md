# 模型预测问题诊断与修复方案

## 问题诊断结果

### 发现的主要 Bug

#### 1. ⚠️ 特征缩放器未保存/加载 (CRITICAL)

**问题描述:**
- 训练时使用 `StandardScaler().fit()` 对特征进行标准化
- 验证时使用假的缩放器（零均值、单位方差）
- 导致输入特征分布完全不同，预测结果严重偏差

**影响:**
- 实际收益率: `+0.000421` (+0.04%)
- 预测 P50: `-0.217862` (-21.78%) ❌
- 误差超过 500 倍！

**修复状态:** ✅ 已修复
- 训练时保存 `feature_scaler.pkl`
- 验证时加载正确的 scaler

#### 2. ⚠️ Quantile 输出顺序问题

**问题描述:**
- 正确顺序应该是: P10 < P50 < P90
- 实际输出顺序混乱: P10 < P90 < P50

**统计数据:**
```
Horizon 300s:
  P10<P50: 509/509 ✓
  P50<P90: 0/509 ✗
  正确顺序: 0/509 ✗
```

**原因分析:**
模型训练时的 quantile loss 没有正确约束输出顺序。需要:
- 添加 quantile 单调性约束
- 或者使用 sort 操作确保 P10 < P50 < P90

#### 3. ⚠️ Address Features 使用问题

**问题描述:**
- 训练时: 使用真实的 address features (20维)
- 验证时: 使用 dummy address features (全0)

这导致验证时的特征分布与训练时不一致。

**临时解决方案:**
验证时使用 dummy features 是可接受的，但需要:
1. 确保特征缩放正确
2. 或者仅在 market features 上训练模型

## 修复方案

### 快速修复 (已实现)

1. **训练时保存 scaler**
```python
# main.py line 1732-1736
import joblib
scaler_path = Path("./temp-data/feature_scaler.pkl")
joblib.dump(feature_scaler, scaler_path)
```

2. **验证时加载 scaler**
```python
# main.py line 1351-1369
import joblib
scaler_path = Path("./temp-data/feature_scaler.pkl")
if scaler_path.exists():
    feature_scaler = joblib.load(scaler_path)
else:
    logger.warning("Using dummy scaler - predictions will be inaccurate!")
```

### 需要重新训练

由于当前的 scaler 是基于训练数据计算的，而验证时使用了错误的 scaler，需要重新训练：

```bash
# 1. 重新训练模型 (会保存新的 scaler)
cd agents/hype-trading-agent/demo/0_0_1
uv run python main.py

# 2. 运行验证
uv run python main.py --verify-only

# 3. 启动 dashboard
uv run python verify_api.py
```

## 优化建议

### 1. 修复 Quantile 顺序问题

在模型输出后添加 sort 约束:

```python
class MultiTaskQuantileModel(nn.Module):
    def forward(self, x: torch.Tensor) -> dict[int, torch.Tensor]:
        shared = self.backbone(x)
        outputs = {}
        for h in self.horizons:
            pred = self.heads[f"h_{h}"](shared)
            # 确保 P10 < P50 < P90
            pred_sorted, _ = torch.sort(pred, dim=1)
            outputs[h] = pred_sorted
        return outputs
```

### 2. 改进特征工程

当前特征维度为 41 (20 address + 23 market - 2 = 41)

建议:
- 增加更多市场特征 (波动率、动量指标等)
- 添加时间特征 (一天中的时刻、星期几)
- 添加跨时间窗口特征

### 3. 改进训练策略

- 使用更长的训练历史 (当前 1 天)
- 增加 training epochs
- 调整学习率调度
- 使用更多的正则化

### 4. 仅使用 Market Features 的简化版本

如果 address features 在验证时不可用，可以训练一个仅使用 market features 的模型:

```python
# 仅使用 market features (23维)
market_feature_extractor = MarketFeatureExtractor()
market_features_only = True

# 修改 CONFIG
CONFIG["use_address_features"] = False
```

## 下一步行动

1. ✅ **立即执行**: 重新训练模型 (保存正确的 scaler)
2. **后续优化**: 修复 quantile 顺序问题
3. **长期改进**: 添加更多特征，改进模型架构

## 验证指标目标

当前指标:
- Direction Accuracy: ~49% (接近随机)
- Coverage [P10, P90]: 0% (完全错误)
- Pinball Loss: 0.112 (P50, 5分钟)

目标指标:
- Direction Accuracy: >60%
- Coverage [P10, P90]: 70-90%
- Pinball Loss: <0.01
