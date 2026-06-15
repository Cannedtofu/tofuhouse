"""
实际费用审计 — 阿里云 DashScope 版
=====================================
读取已下载的 Batch 结果 JSONL 文件，从 usage 字段提取真实 Token 用量并计算费用。
也可补下载尚未缓存到本地的结果文件。
"""

import json
import os
import glob
from openai import OpenAI

# ==================== 核心配置 ====================
API_KEY      = ""         # 填入阿里云 DashScope API Key
RAW_DATA_DIR = "raw_batch_results"
JOB_LOG_FILE = "alibaba_batch_log.txt"

# 实际计费价格（USD / 1M tokens），请以阿里云官网最新公告为准
# 此处以 qwen-plus 正式价为示例（Batch 任务通常有 50% 折扣）
PRICE_IN_1M  = 0.07    # qwen-plus 输入
PRICE_OUT_1M = 0.21    # qwen-plus 输出
# ==================================================

client   = OpenAI(
    api_key=API_KEY,
    base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def ensure_raw_files() -> None:
    """对日志中已完成但本地尚无缓存的任务，补充下载结果文件。"""
    log_path = os.path.join(BASE_DIR, JOB_LOG_FILE)
    if not os.path.exists(log_path):
        return

    save_dir = os.path.join(BASE_DIR, RAW_DATA_DIR)
    os.makedirs(save_dir, exist_ok=True)

    downloaded = 0
    with open(log_path, "r", encoding="utf-8") as f:
        for line in f:
            if ":" not in line:
                continue
            _, job_id = line.strip().split(":", 1)
            job_id    = job_id.strip()
            if not job_id:
                continue

            safe_name  = job_id.replace("/", "_").replace(":", "")
            local_path = os.path.join(save_dir, f"raw_{safe_name}.jsonl")

            if os.path.exists(local_path):
                continue  # 已有缓存，跳过

            print(f"⬇️  补下载: {job_id} ...")
            try:
                job = client.batches.retrieve(job_id)
                if job.status == "completed" and job.output_file_id:
                    content = client.files.content(job.output_file_id).read()
                    with open(local_path, "wb") as out:
                        out.write(content)
                    downloaded += 1
                else:
                    print(f"   ⏳ 状态: {job.status}，跳过。")
            except Exception as e:
                print(f"   ❌ 下载失败: {e}")

    if downloaded:
        print(f"✅ 补下载了 {downloaded} 个文件。\n")


def audit_local_files() -> None:
    ensure_raw_files()

    raw_dir    = os.path.join(BASE_DIR, RAW_DATA_DIR)
    jsonl_list = glob.glob(os.path.join(raw_dir, "*.jsonl"))

    if not jsonl_list:
        print(f"❌ {RAW_DATA_DIR}/ 目录下没有 JSONL 文件。")
        return

    print(f"📊 审计 {len(jsonl_list)} 个结果文件...\n")

    total_input  = 0
    total_output = 0
    total_valid  = 0

    for fpath in sorted(jsonl_list):
        fname    = os.path.basename(fpath)
        f_input  = 0
        f_output = 0

        with open(fpath, "r", encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    data = json.loads(line)
                    # OpenAI 兼容格式：response.body.usage
                    usage = (
                        data.get("response", {})
                            .get("body", {})
                            .get("usage", {})
                    )
                    if not usage:
                        # 备用路径
                        usage = data.get("usage", {})

                    if usage:
                        f_input  += usage.get("prompt_tokens",     usage.get("input_tokens",  0))
                        f_output += usage.get("completion_tokens", usage.get("output_tokens", 0))
                        total_valid += 1
                except Exception:
                    pass

        total_input  += f_input
        total_output += f_output

    cost_in  = (total_input  / 1_000_000) * PRICE_IN_1M
    cost_out = (total_output / 1_000_000) * PRICE_OUT_1M
    total    = cost_in + cost_out

    print("=" * 52)
    print(f"✅ 审计完成（共 {total_valid:,} 条有效记录）")
    print("-" * 52)
    print(f"输入 Tokens  : {total_input:>12,}   × ${PRICE_IN_1M}/1M")
    print(f"输出 Tokens  : {total_output:>12,}   × ${PRICE_OUT_1M}/1M")
    print("-" * 52)
    print(f"💰 实际总费用 : ${total:>12.4f} USD")
    print(f"   (约合人民币: ¥{total * 7.2:.2f})")
    print("=" * 52)
    print("⚠️  以阿里云账单实际扣款为准，以上为估算值。")


if __name__ == "__main__":
    audit_local_files()
