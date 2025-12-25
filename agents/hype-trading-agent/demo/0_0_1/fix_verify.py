"""
修复验证pipeline的bug

主要修复:
1. 训练时保存 feature_scaler
2. 验证时加载正确的 feature_scaler
3. 使用更简单的验证方法（仅用market features）
"""

import joblib
import torch
import numpy as np
from pathlib import Path
from sklearn.preprocessing import StandardScaler

# 检查训练时使用的特征缩放器
scaler_path = Path("./temp-data/feature_scaler.pkl")
if scaler_path.exists():
    scaler = joblib.load(scaler_path)
    print(f"✓ 找到保存的 scaler")
    print(f"  mean shape: {scaler.mean_.shape}")
    print(f"  scale shape: {scaler.scale_.shape}")
    print(f"  mean[:5]: {scaler.mean_[:5]}")
    print(f"  scale[:5]: {scaler.scale_[:5]}")
else:
    print(f"✗ 没有找到保存的 scaler")

# 检查模型
model_path = Path("./best_model.pt")
if model_path.exists():
    checkpoint = torch.load(model_path, map_location="cpu")
    print(f"\n✓ 模型输入维度: {checkpoint['backbone.0.weight'].shape[1]}")
else:
    print(f"✗ 没有找到模型")

# 分析问题
print(f"\n=== 问题分析 ===")
print(f"1. 训练时使用的特征维度: {checkpoint['backbone.0.weight'].shape[1]}")
print(f"2. 验证时使用的特征维度: 41 (market only)")
print(f"3. 缩放器问题: 验证时使用零均值/单位方差，与训练不一致")
print(f"4. Dummy address features: 全为0，与训练分布不一致")

print(f"\n=== 修复方案 ===")
print(f"方案1: 修改验证代码，使用训练时保存的 scaler")
print(f"方案2: 重新训练，仅使用 market features")
print(f"方案3: 在验证数据上也提取 address features（需要trades数据）")

print(f"\n=== Quantile顺序问题 ===")
print(f"检查模型输出层权重...")
for h in [300, 900, 1800]:
    weight = checkpoint[f'heads.h_{h}.weight'].cpu().numpy()
    bias = checkpoint[f'heads.h_{h}.bias'].cpu().numpy()
    print(f"Horizon {h}s:")
    print(f"  weight shape: {weight.shape}")
    print(f"  P10 output weight: {weight[0, :3]}...")
    print(f"  P50 output weight: {weight[1, :3]}...")
    print(f"  P90 output weight: {weight[2, :3]}...")
    print(f"  P10 bias: {bias[0]:.4f}")
    print(f"  P50 bias: {bias[1]:.4f}")
    print(f"  P90 bias: {bias[2]:.4f}")
