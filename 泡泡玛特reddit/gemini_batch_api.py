"""
Popmart Reddit 批量分析 — 阿里云 DashScope 版
=============================================
将 Reddit 数据通过阿里云 OpenAI 兼容接口提交为 Batch 任务。
每次运行前请确保 alibaba_batch_log.txt 已清空或备份（旧 Job ID 会引起混淆）。
"""

import pandas as pd
import json
import os
from openai import OpenAI
import time

# ==================== 核心配置 ====================
API_KEY   = ""          # 填入阿里云 DashScope API Key（控制台 → API-KEY 管理）
FILE_NAME = "popmart_v3.0_2026-06-09.xlsx"   # 待分析的 Excel 文件（运行爬虫后更新此名称）

# 模型选择（根据需求取消注释）:
#   qwen-turbo   — 速度最快，成本最低，适合大批量初筛
#   qwen-plus    — 性能与成本均衡（推荐）
#   qwen-max     — 最高质量，成本最高
#   qwen-long    — 超长上下文（> 32K token），适合极长帖子
MODEL_NAME = "qwen-plus"

ROWS_PER_BATCH = 2000   # 每个 Batch 任务的行数（DashScope 建议 ≤ 50,000 行/文件）

# 指定本次要提交的批次编号，例如 range(1, 7) 表示第 1-6 批
# 运行前先查看 FILE_NAME 总行数：total_batches = ceil(total_rows / ROWS_PER_BATCH)
TARGET_BATCHES = list(range(1, 20))

WAIT_TIME_ON_429 = 200  # 触发限额后等待秒数
JOB_LOG_FILE     = "alibaba_batch_log.txt"   # 与旧 Gemini 日志分开存放
# ==================================================

client = OpenAI(
    api_key=API_KEY,
    base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
)
BASE_DIR   = os.path.dirname(os.path.abspath(__file__))
INPUT_PATH = os.path.join(BASE_DIR, FILE_NAME)

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
3.  "emotions":            Object with exactly 3 keys: {"emotion1": score, "emotion2": score, "emotion3": score}
4.  "ambivalence":         Float (0.0 to 1.0)
5.  "subjectivity":        Enum ["objective", "subjective"]
6.  "core_topic":          String (the specific topic of discussion)
7.  "aspect":              String (the entity or feature being evaluated)
8.  "intent":              Enum ["inquiry", "complaint", "praise", "suggestion", "social_chat", "sarcastic_comment"]
9.  "stance":              Enum ["support", "against", "neutral", "unclear"]
10. "churn_risk":          Enum ["high", "medium", "low", "N/A"]
11. "urgency":             Enum ["high", "medium", "low"]
12. "sarcasm":             Boolean
13. "toxicity":            Float (0.0 to 1.0)
14. "tone":                Enum ["formal", "humorous", "aggressive", "polite", "ironic"]
15. "certainty":           Enum ["certain", "tentative", "speculative"]
16. "engagement":          Float (0.0 to 1.0)
17. "influence_potential": Float (0.0 to 1.0)
"""


def run_production_batches() -> None:
    if not os.path.exists(INPUT_PATH):
        print(f"❌ 未找到输入文件：{INPUT_PATH}")
        return

    print("正在读取数据并构建上下文索引...")
    df       = pd.read_excel(INPUT_PATH)
    text_map = dict(zip(df["ID"].astype(str), df["内容正文"].astype(str)))
    total    = len(df)
    print(f"   共 {total:,} 行，每批 {ROWS_PER_BATCH} 行，"
          f"预计 {-(-total // ROWS_PER_BATCH)} 批。")

    for i in range(0, total, ROWS_PER_BATCH):
        batch_num = i // ROWS_PER_BATCH + 1

        if batch_num not in TARGET_BATCHES:
            continue

        print(f"\n--- 准备第 {batch_num} 批 ---")
        batch_df  = df.iloc[i : i + ROWS_PER_BATCH]
        jsonl_path = os.path.join(BASE_DIR, f"popmart_part_{batch_num}.jsonl")

        # 1. 生成 OpenAI Batch 格式的 JSONL 文件
        with open(jsonl_path, "w", encoding="utf-8") as f:
            for _, row in batch_df.iterrows():
                parent_text = text_map.get(str(row["父级ID"]), "N/A (Root Thread)")
                user_content = (
                    f"[Thread Title]: {row['所属标题']}\n"
                    f"[Parent Comment]: {parent_text}\n"
                    f"[Target Comment]: {row['内容正文']}\n\n"
                    "Analyze the [Target Comment] and return the JSON object now."
                )
                line = {
                    "custom_id": f"row_{row['ID']}",
                    "method":    "POST",
                    "url":       "/v1/chat/completions",
                    "body": {
                        "model": MODEL_NAME,
                        "messages": [
                            {"role": "system", "content": SYSTEM_INSTRUCTION},
                            {"role": "user",   "content": user_content},
                        ],
                        "response_format": {"type": "json_object"},
                    },
                }
                f.write(json.dumps(line, ensure_ascii=False) + "\n")

        # 2. 提交批处理任务（带自动重试）
        submitted = False
        while not submitted:
            try:
                print(f"   上传 JSONL 文件: {os.path.basename(jsonl_path)}...")
                with open(jsonl_path, "rb") as fp:
                    uploaded = client.files.create(file=fp, purpose="batch")

                print(f"   启动 Batch 任务 #{batch_num}...")
                job = client.batches.create(
                    input_file_id     = uploaded.id,
                    endpoint          = "/v1/chat/completions",
                    completion_window = "24h",
                    metadata          = {"description": f"Popmart_Part_{batch_num}"},
                )

                print(f"   ✅ 提交成功！Batch ID: {job.id}")

                # 记录 Job ID
                log_path = os.path.join(BASE_DIR, JOB_LOG_FILE)
                with open(log_path, "a", encoding="utf-8") as lf:
                    lf.write(f"Batch_{batch_num}: {job.id}\n")

                submitted = True

            except Exception as e:
                msg = str(e)
                if "429" in msg or "RateLimitError" in msg or "RESOURCE_EXHAUSTED" in msg:
                    print(f"   🛑 触发限额，等待 {WAIT_TIME_ON_429}s 后重试...")
                    time.sleep(WAIT_TIME_ON_429)
                else:
                    print(f"   ❌ 非限额错误，跳过第 {batch_num} 批: {e}")
                    break

    print(f"\n🎉 所有指定批次已提交。Job ID 记录于 {JOB_LOG_FILE}")


if __name__ == "__main__":
    run_production_batches()
