import pandas as pd
import math
import os

# ==================== 配置区域 ====================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
FILE_NAME = "popmart_test.xlsx"
INPUT_PATH = os.path.join(BASE_DIR, FILE_NAME)

# 2026 Gemini 1.5 Flash Batch API 预估价格 (USD)
# 提示：Batch 模式通常比实时模式优惠 50%
PRICE_PER_1M_INPUT_TOKENS = 0.0375 
PRICE_PER_1M_OUTPUT_TOKENS = 0.15

# 你提供的最新版系统指令
SYSTEM_INSTRUCTION = """
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
1.  "polarity": Float (-1.0 to 1.0)
2.  "intensity": Float (0.0 to 1.0)
3.  "emotions": Object {{"emotion1": score, "emotion2": score, "emotion3": score}}
4.  "ambivalence": Float (0.0 to 1.0)
5.  "subjectivity": Enum ["objective", "subjective"]
6.  "core_topic": String (the specific topic of discussion)
7.  "aspect": String (the entity or feature being evaluated)
8.  "intent": Enum ["inquiry", "complaint", "praise", "suggestion", "social_chat", "sarcastic_comment"]
9.  "stance": Enum ["support", "against", "neutral", "unclear"]
10. "churn_risk": Enum ["high", "medium", "low", "N/A"]
11. "urgency": Enum ["high", "medium", "low"]
12. "sarcasm": Boolean
13. "toxicity": Float (0.0 to 1.0)
14. "tone": Enum ["formal", "humorous", "aggressive", "polite", "ironic"]
15. "certainty": Enum ["certain", "tentative", "speculative"]
16. "engagement": Float (0.0 to 1.0)
17. "influence_potential": Float (0.0 to 1.0)
"""
# ==================================================

def run_estimation():
    if not os.path.exists(INPUT_PATH):
        print(f"❌ 错误：在路径 {INPUT_PATH} 未找到文件。")
        return

    print("正在加载 Excel 文件并构建索引...")
    df = pd.read_excel(INPUT_PATH)
    
    # 统一将 ID 转换为字符串以防匹配失败
    df['ID'] = df['ID'].astype(str)
    df['父级ID'] = df['父级ID'].astype(str)
    df['内容正文'] = df['内容正文'].astype(str)
    
    # 构建上下文映射表
    text_map = dict(zip(df['ID'], df['内容正文']))
    
    total_input_chars = 0
    sample_preview = ""
    
    print("正在模拟上下文拼接并统计长度...")
    for idx, row in df.iterrows():
        root_title = str(row.get('所属标题', 'N/A'))
        parent_id = row['父级ID']
        target_text = row['内容正文']
        
        # 核心：获取父评论文本
        parent_text = text_map.get(parent_id, "N/A (Root Thread)")
        
        # 模拟模型接收到的最终拼接字符串
        full_prompt_sample = f"{SYSTEM_INSTRUCTION}\n[Thread Title]: {root_title}\n[Parent Comment]: {parent_text}\n[Target Comment]: {target_text}\nTask: Analyze the [Target Comment] now."
        
        total_input_chars += len(full_prompt_sample)
        
        # 选取第 20 行（通常已有父子关系）作为预览展示
        if idx == 20:
            sample_preview = full_prompt_sample

    # Token 计算逻辑：
    # 输入：按 1 Token ≈ 4 个英文字符计算
    # 输出：17 个维度的精细 JSON 约占 850-1000 Tokens
    est_input_tokens = total_input_chars / 4
    est_output_tokens = len(df) * 900 
    
    total_cost = ((est_input_tokens / 1_000_000) * PRICE_PER_1M_INPUT_TOKENS) + \
                 ((est_output_tokens / 1_000_000) * PRICE_PER_1M_OUTPUT_TOKENS)

    print("\n" + "="*30 + " 核心输入样本确认 " + "="*30)
    print(sample_preview if sample_preview else "未找到合适的样本行。")
    print("="*78)

    print(f"\n📈 预估详情 (基于 Gemini 1.5 Flash Batch API):")
    print(f"   - 文件行数: {len(df):,}")
    print(f"   - 预估输入总 Tokens: {math.ceil(est_input_tokens):,}")
    print(f"   - 预估输出总 Tokens: {math.ceil(est_output_tokens):,}")
    print(f"   - 预计总费用: ${total_cost:.4f} USD")
    print(f"   - (约合人民币: ¥{total_cost * 7.2:.2f})\n")

if __name__ == "__main__":
    run_estimation()