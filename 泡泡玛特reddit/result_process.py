"""
Popmart Reddit 结果合并 — 阿里云 DashScope 版
=============================================
从 alibaba_batch_log.txt 加载 Batch Job ID，
下载已完成任务的结果文件，解析并合并回原始 Excel。
"""

import pandas as pd
import json
import os
import time
from openai import OpenAI

# ==================== 核心配置 ====================
API_KEY        = ""      # 填入阿里云 DashScope API Key
ORIGINAL_EXCEL = "popmart_v3.0_2026-06-09.xlsx"   # 原始爬取数据（与 gemini_batch_api.py 一致）
OUTPUT_EXCEL   = "popmart_analysis_FINAL.xlsx"
JOB_LOG_FILE   = "alibaba_batch_log.txt"
RAW_DATA_DIR   = "raw_batch_results"
# ==================================================

client   = OpenAI(
    api_key=API_KEY,
    base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def load_job_ids() -> list[str]:
    """从日志文件读取所有 Batch Job ID。"""
    log_path = os.path.join(BASE_DIR, JOB_LOG_FILE)
    if not os.path.exists(log_path):
        print(f"⚠️  未找到日志文件: {log_path}")
        return []

    ids: list[str] = []
    with open(log_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if ":" not in line:
                continue
            try:
                _, job_id = line.split(":", 1)
                job_id = job_id.strip()
                if job_id:
                    ids.append(job_id)
            except ValueError:
                continue

    ids = list(dict.fromkeys(ids))   # 去重，保留顺序
    print(f"从 {JOB_LOG_FILE} 加载了 {len(ids)} 个 Job ID。")
    return ids


def parse_batch_content(content_bytes: bytes, job_id: str) -> list[dict]:
    """
    解析 Batch 结果 JSONL（OpenAI 兼容格式）。
    每行格式：
    {
      "custom_id": "row_t3_xxx",
      "response": {
        "status_code": 200,
        "body": {
          "choices": [{"message": {"content": "..."}}],
          "usage": {"prompt_tokens": N, "completion_tokens": N}
        }
      },
      "error": null
    }
    """
    extracted: list[dict] = []
    lines = content_bytes.decode("utf-8").splitlines()
    valid_count = 0

    for line in lines:
        if not line.strip():
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue

        # 跳过有错误的行
        if data.get("error"):
            continue

        response = data.get("response", {})
        if response.get("status_code") != 200:
            continue

        body = response.get("body", {})
        choices = body.get("choices", [])
        if not choices:
            continue

        raw_id   = data.get("custom_id", "").replace("row_", "")
        json_text = choices[0]["message"]["content"]

        # 清理偶发的 markdown 代码块
        json_text = json_text.replace("```json", "").replace("```", "").strip()

        try:
            result = json.loads(json_text)
            result["ID"] = raw_id
            extracted.append(result)
            valid_count += 1
        except json.JSONDecodeError:
            print(f"   ⚠️  JSON 解析失败 (ID: {raw_id})")

    print(f"   → 成功提取 {valid_count} 条有效记录。")
    return extracted


def merge_results() -> None:
    # 1. 加载 Job ID
    job_ids = load_job_ids()
    if not job_ids:
        print("❌ 无 Job ID，退出。")
        return

    # 2. 读取原始 Excel
    input_path = os.path.join(BASE_DIR, ORIGINAL_EXCEL)
    if not os.path.exists(input_path):
        print(f"❌ 原始文件未找到: {input_path}")
        return

    print(f"读取原始数据: {ORIGINAL_EXCEL}...")
    df_original = pd.read_excel(input_path)
    df_original["ID"] = df_original["ID"].astype(str)

    # 3. 确保原始结果保存目录存在
    raw_dir = os.path.join(BASE_DIR, RAW_DATA_DIR)
    os.makedirs(raw_dir, exist_ok=True)

    all_extracted: list[dict] = []

    # 4. 逐个处理 Job
    for job_id in job_ids:
        print(f"\n检查 Job: {job_id}")
        try:
            job = client.batches.retrieve(job_id)
            status = job.status   # "completed" | "failed" | "in_progress" | etc.

            if status == "completed":
                output_file_id = job.output_file_id
                if not output_file_id:
                    print(f"   ⚠️  任务完成但无输出文件。")
                    continue

                # 保存原始 JSONL
                safe_name  = job_id.replace("/", "_").replace(":", "")
                local_path = os.path.join(raw_dir, f"raw_{safe_name}.jsonl")

                if os.path.exists(local_path):
                    print(f"   ✅ 已有本地缓存，直接解析: {os.path.basename(local_path)}")
                    with open(local_path, "rb") as f:
                        content = f.read()
                else:
                    print(f"   ⬇️  下载结果文件 ({output_file_id})...")
                    content = client.files.content(output_file_id).read()
                    with open(local_path, "wb") as f:
                        f.write(content)
                    print(f"   💾 已保存到: {os.path.basename(local_path)}")

                records = parse_batch_content(content, job_id)
                all_extracted.extend(records)

            elif status in ("failed", "expired", "cancelled"):
                print(f"   ❌ Job 状态: {status}，跳过。")
            else:
                print(f"   ⏳ Job 状态: {status}（尚未完成），跳过。")

        except Exception as e:
            print(f"   ❌ 处理 {job_id} 时出错: {e}")

    # 5. 合并并输出
    if not all_extracted:
        print("\n😭 本次没有提取到任何分析结果。")
        return

    print(f"\n合并 {len(all_extracted):,} 条结果到原始数据...")
    df_results = pd.DataFrame(all_extracted)
    df_results = df_results.drop_duplicates(subset=["ID"], keep="last")

    df_merged = pd.merge(df_original, df_results, on="ID", how="left")

    output_path = os.path.join(BASE_DIR, OUTPUT_EXCEL)
    df_merged.to_excel(output_path, index=False)
    print(f"🎉 完成！最终文件已保存: {OUTPUT_EXCEL}")
    print(f"   总行数: {len(df_merged):,}  |  "
          f"已分析: {df_merged['polarity'].notna().sum():,}  |  "
          f"待分析: {df_merged['polarity'].isna().sum():,}")


if __name__ == "__main__":
    merge_results()
