"""
批处理成本预估 — 阿里云 DashScope
===================================
在提交 Batch 任务前运行，根据模型和文件行数估算总费用。
价格以 USD 计算（阿里云官网通常以 RMB 报价，需转换）。

参考价格（2026 年，请以官网最新公告为准）：
  https://help.aliyun.com/zh/model-studio/getting-started/models

  模型          输入 ($/1M tokens)  输出 ($/1M tokens)
  qwen-turbo        ~$0.06              ~$0.06
  qwen-plus         ~$0.14              ~$0.42
  qwen-max          ~$2.40              ~$9.60
  qwen-long         ~$0.09              ~$0.09
"""

import pandas as pd
import math
import os

# ==================== 配置区域 ====================
BASE_DIR   = os.path.dirname(os.path.abspath(__file__))
FILE_NAME  = "popmart_v3.0_2026-06-09.xlsx"   # 待分析的 Excel 文件
INPUT_PATH = os.path.join(BASE_DIR, FILE_NAME)

# 选择与 gemini_batch_api.py 相同的模型（Batch 任务通常享受 50% 折扣）
MODEL_NAME = "qwen-plus"

# 预估价格（USD / 1M tokens），含 Batch 50% 折扣
PRICE_PER_1M_INPUT  = 0.07   # qwen-plus 输入
PRICE_PER_1M_OUTPUT = 0.21   # qwen-plus 输出
# ==================================================

SYSTEM_INSTRUCTION = """\
# ROLE
You are an expert NLP researcher specializing in Reddit community dynamics and consumer sentiment.

# MANDATE
Your sole task is to analyze the [Target Comment] and output a JSON object.
You MUST provide a value for EVERY ONE of the 17 keys listed below.
Do not omit any key. Do not add extra keys.

# OUTPUT CONSTRAINTS
- Format: Strictly JSON.
- No markdown code blocks (no ```json).
- Missing values: If a dimension is absolutely not applicable, use "N/A" for strings or 0.0 for floats.
- Language: All values must be in English.

# DATA DIMENSIONS (The 17 Required Keys)
1.  "polarity":            Float (-1.0 to 1.0)
2.  "intensity":           Float (0.0 to 1.0)
3.  "emotions":            Object with exactly 3 keys
4.  "ambivalence":         Float (0.0 to 1.0)
5.  "subjectivity":        Enum ["objective", "subjective"]
6.  "core_topic":          String
7.  "aspect":              String
8.  "intent":              Enum [...]
9.  "stance":              Enum [...]
10. "churn_risk":          Enum [...]
11. "urgency":             Enum [...]
12. "sarcasm":             Boolean
13. "toxicity":            Float (0.0 to 1.0)
14. "tone":                Enum [...]
15. "certainty":           Enum [...]
16. "engagement":          Float (0.0 to 1.0)
17. "influence_potential": Float (0.0 to 1.0)
"""


def run_estimation() -> None:
    if not os.path.exists(INPUT_PATH):
        print(f"❌ 文件未找到: {INPUT_PATH}")
        return

    print(f"加载数据文件: {FILE_NAME}...")
    df = pd.read_excel(INPUT_PATH)
    df["ID"]     = df["ID"].astype(str)
    df["父级ID"] = df["父级ID"].astype(str)
    df["内容正文"] = df["内容正文"].astype(str)

    text_map = dict(zip(df["ID"], df["内容正文"]))

    total_input_chars = 0
    sample_prompt     = ""

    print("模拟提示词构建，统计字符数...")
    for idx, row in df.iterrows():
        parent_text  = text_map.get(row["父级ID"], "N/A (Root Thread)")
        full_prompt  = (
            f"{SYSTEM_INSTRUCTION}\n"
            f"[Thread Title]: {row.get('所属标题', 'N/A')}\n"
            f"[Parent Comment]: {parent_text}\n"
            f"[Target Comment]: {row['内容正文']}\n\n"
            "Analyze the [Target Comment] and return the JSON object now."
        )
        total_input_chars += len(full_prompt)
        if idx == 20:
            sample_prompt = full_prompt

    # 估算 token 数：英文约 1 token ≈ 4 字符，中文约 1 token ≈ 1.5 字符
    # 保守起见用 3.5 作折中，但这里内容主要为英文
    est_input_tokens  = total_input_chars / 4
    est_output_tokens = len(df) * 900    # 17 维 JSON ≈ 850-950 tokens/条

    cost_input  = (est_input_tokens  / 1_000_000) * PRICE_PER_1M_INPUT
    cost_output = (est_output_tokens / 1_000_000) * PRICE_PER_1M_OUTPUT
    total_cost  = cost_input + cost_output

    print("\n" + "="*35 + " 提示词样本 " + "="*35)
    print(sample_prompt or "（未找到第 20 行样本）")
    print("="*80)

    print(f"\n📈 成本预估 (模型: {MODEL_NAME}, 含 Batch 50% 折扣):")
    print(f"   文件行数          : {len(df):>10,}")
    print(f"   预估输入 Tokens   : {math.ceil(est_input_tokens):>10,}  × ${PRICE_PER_1M_INPUT}/1M")
    print(f"   预估输出 Tokens   : {math.ceil(est_output_tokens):>10,}  × ${PRICE_PER_1M_OUTPUT}/1M")
    print(f"   ─────────────────────────────────────────")
    print(f"   预计总费用        :    ${total_cost:.4f} USD")
    print(f"   (约合人民币       :    ¥{total_cost * 7.2:.2f})\n")
    print("⚠️  注意：以上为估算值，实际费用以阿里云账单为准。")
    print("   请在提交前前往控制台确认最新模型报价。")


if __name__ == "__main__":
    run_estimation()
