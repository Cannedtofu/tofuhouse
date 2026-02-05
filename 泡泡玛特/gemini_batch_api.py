import pandas as pd
import json
import os
from google import genai
import time 

# ==================== 核心配置 ====================
API_KEY = "AIzaSyCPCdPj75PoBjfhX5xzGHcw2A1Ei_N-qkU"
FILE_NAME = "popmart_analyze_test.xlsx"
ROWS_PER_BATCH = 2000 
# 确保使用 2026 年支持 Batch 的模型名称
MODEL_NAME = "gemini-2.5-flash" 

client = genai.Client(api_key=API_KEY)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
INPUT_PATH = os.path.join(BASE_DIR, FILE_NAME)

# --- 自适应与记录配置 ---
WAIT_TIME_ON_429 = 200  # 触发限额后的暂停秒数
JOB_LOG_FILE = "batch_job_log.txt"  # 存储 Job ID 的文件名
# -----------------------

# --- 关键配置：指定本次要跑的任务编号 ---
# 例如：range(3, 12) 会生成 [3, 4, 5, 6, 7, 8, 9, 10, 11]
TARGET_BATCHES = list(range(3, 12)) 
# ------------------------------------

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
# =================================================

def run_production_batches():
    if not os.path.exists(INPUT_PATH):
        print(f"❌ 错误：未找到文件 {INPUT_PATH}")
        return

    print(f"正在读取数据并构建语境树...")
    df = pd.read_excel(INPUT_PATH)
    text_map = dict(zip(df['ID'].astype(str), df['内容正文'].astype(str)))

    total_rows = len(df)
    
    # 遍历所有可能的批次
    for i in range(0, total_rows, ROWS_PER_BATCH):
        batch_num = (i // ROWS_PER_BATCH) + 1
        
        # 判定：如果当前批次不在目标列表中，则跳过
        if batch_num not in TARGET_BATCHES:
            continue
            
        print(f"\n--- 准备处理第 {batch_num} 组任务 ---")
        batch_df = df.iloc[i : i + ROWS_PER_BATCH]
        jsonl_path = os.path.join(BASE_DIR, f"popmart_part_{batch_num}.jsonl")
        
        # 1. 生成 JSONL 文件
        with open(jsonl_path, 'w', encoding='utf-8') as f:
            for _, row in batch_df.iterrows():
                parent_text = text_map.get(str(row['父级ID']), "N/A (Root Thread)")
                
                line_data = {
                    "custom_id": f"row_{row['ID']}",
                    "request": {
                        "contents": [{
                            "parts": [{"text": f"{SYSTEM_INSTRUCTION}\n[Title]: {row['所属标题']}\n[Parent]: {parent_text}\n[Target]: {row['内容正文']}"}]
                        }],
                        "generation_config": {"response_mime_type": "application/json"}
                    }
                }
                f.write(json.dumps(line_data) + '\n')
        
        # 2. 提交批处理任务 (带自适应重试机制)
        submitted = False
        while not submitted:
            try:
                print(f"正在上传文件: {jsonl_path}...")
                uploaded_file = client.files.upload(
                    file=jsonl_path,
                    config={'mime_type': 'application/jsonl'} 
                )
                
                print(f"正在启动任务 {batch_num}...")
                job = client.batches.create(
                    model=MODEL_NAME,
                    src=uploaded_file.name,
                    config={'display_name': f"Popmart_Part_{batch_num}"}
                )
                
                job_id = job.name
                print(f"✅ 成功! Job ID: {job_id}")
                
                # 记录 Job ID 到文本文件 [追加模式]
                with open(os.path.join(BASE_DIR, JOB_LOG_FILE), "a", encoding="utf-8") as log_f:
                    log_f.write(f"Batch_{batch_num}: {job_id}\n")
                
                submitted = True # 标记成功，跳出当前 while 循环

            except Exception as e:
                err_msg = str(e)
                if "429" in err_msg or "RESOURCE_EXHAUSTED" in err_msg:
                    print(f"🛑 触发用量限制。暂停 {WAIT_TIME_ON_429} 秒后重新尝试提交第 {batch_num} 组...")
                    time.sleep(WAIT_TIME_ON_429)
                else:
                    print(f"❌ 任务 {batch_num} 发生非限额类错误: {e}")
                    # 对于非 429 错误，通常建议跳过或检查代码，这里选择跳过该批次
                    break 

    print(f"\n🎉 指定的批次处理尝试完毕。Job ID 已记录至 {JOB_LOG_FILE}")


if __name__ == "__main__":
    run_production_batches()