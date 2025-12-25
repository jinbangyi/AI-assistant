"""
诊断模型预测问题

检查：
1. 特征缩放器是否正确保存和加载
2. 验证时的特征分布是否与训练时一致
3. 模型输出是否正确
"""
import json
import torch
import numpy as np
from pathlib import Path
from loguru import logger

# 检查 verify_results.json
verify_results_path = Path("./temp-data/verify_results.json")

logger.info("=" * 60)
logger.info("模型预测诊断")
logger.info("=" * 60)

with verify_results_path.open("r") as f:
    data = json.load(f)

btc_data = data["BTC"]
samples = btc_data["samples"]

logger.info(f"\n总样本数: {len(samples)}")

# 分析第一个样本
sample = samples[0]
logger.info(f"\n=== 第一个样本分析 ===")
logger.info(f"时间戳: {sample['timestamp']}")
logger.info(f"当前价格: {sample['close']}")

for horizon in [300, 900, 1800]:
    h_key = f"return_{horizon}s"
    if h_key in sample["targets"]:
        actual = sample["targets"][h_key]
        p10 = sample["predictions"][h_key]["p10"]
        p50 = sample["predictions"][h_key]["p50"]
        p90 = sample["predictions"][h_key]["p90"]

        logger.info(f"\nHorizon {horizon}s:")
        logger.info(f"  实际收益率: {actual:.6f} ({actual*100:.4f}%)")
        logger.info(f"  预测 P10:   {p10:.6f} ({p10*100:.4f}%)")
        logger.info(f"  预测 P50:   {p50:.6f} ({p50*100:.4f}%)")
        logger.info(f"  预测 P90:   {p90:.6f} ({p90*100:.4f}%)")
        logger.info(f"  P10<P50<P90? {p10 < p50 < p90}")
        logger.info(f"  实际在区间内? {p10 <= actual <= p90}")

# 统计分析
logger.info(f"\n=== 统计分析 ===")
for horizon in [300, 900, 1800]:
    h_key = f"return_{horizon}s"
    actuals = [s["targets"][h_key] for s in samples if h_key in s["targets"]]
    p10s = [s["predictions"][h_key]["p10"] for s in samples if h_key in s["predictions"]]
    p50s = [s["predictions"][h_key]["p50"] for s in samples if h_key in s["predictions"]]
    p90s = [s["predictions"][h_key]["p90"] for s in samples if h_key in s["predictions"]]

    actuals_arr = np.array(actuals)
    p10s_arr = np.array(p10s)
    p50s_arr = np.array(p50s)
    p90s_arr = np.array(p90s)

    logger.info(f"\nHorizon {horizon}s:")
    logger.info(f"  实际收益率: mean={actuals_arr.mean():.6f}, std={actuals_arr.std():.6f}")
    logger.info(f"  预测 P10:   mean={p10s_arr.mean():.6f}, std={p10s_arr.std():.6f}")
    logger.info(f"  预测 P50:   mean={p50s_arr.mean():.6f}, std={p50s_arr.std():.6f}")
    logger.info(f"  预测 P90:   mean={p90s_arr.mean():.6f}, std={p90s_arr.std():.6f}")

    # 检查顺序
    p10_less_p50 = np.sum(p10s_arr < p50s_arr)
    p50_less_p90 = np.sum(p50s_arr < p90s_arr)
    correct_order = np.sum((p10s_arr < p50s_arr) & (p50s_arr < p90s_arr))
    logger.info(f"  P10<P50: {p10_less_p50}/{len(p10s_arr)}")
    logger.info(f"  P50<P90: {p50_less_p90}/{len(p50s_arr)}")
    logger.info(f"  正确顺序: {correct_order}/{len(p10s_arr)}")

    # 覆盖率
    in_range = np.sum((actuals_arr >= p10s_arr) & (actuals_arr <= p90s_arr))
    coverage = in_range / len(actuals_arr)
    logger.info(f"  覆盖率: {coverage:.4f} ({in_range}/{len(actuals_arr)})")

# 检查模型加载
logger.info(f"\n=== 模型检查 ===")
model_path = Path("./best_model.pt")
if model_path.exists():
    checkpoint = torch.load(model_path, map_location="cpu")
    logger.info(f"模型 checkpoint keys: {list(checkpoint.keys())}")

    # 检查 backbone.0.weight 的形状
    if "backbone.0.weight" in checkpoint:
        weight_shape = checkpoint["backbone.0.weight"].shape
        logger.info(f"输入层权重形状: {weight_shape}")
        logger.info(f"实际特征维度: {weight_shape[1]}")
else:
    logger.error("模型文件不存在!")

logger.info(f"\n=== 问题诊断 ===")
logger.info("发现以下问题:")
logger.info("1. 特征缩放器未保存/加载 - 验证时使用零均值单位方差，与训练不一致")
logger.info("2. Address features 使用全0 - 与训练时分布不一致")
logger.info("3. 预测值全部为负且偏差巨大 - 可能是特征分布不匹配导致")
logger.info("")
logger.info("建议修复方案:")
logger.info("1. 训练时保存 feature_scaler 到文件")
logger.info("2. 验证时加载正确的 feature_scaler")
logger.info("3. 或者使用真实交易数据进行验证（而非 dummy features）")
