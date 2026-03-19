import pandas as pd
import json

# Configuration
input_file = "scraped_data_music_only.jsonl"
output_file = "processed_live_entertainment_data_music_only_260305.xlsx"

def process_data(jsonl_path, excel_path):
    data = []
    
    # 1. Load Data
    try:
        with open(jsonl_path, 'r', encoding='utf-8') as f:
            for line in f:
                if line.strip():
                    data.append(json.loads(line))
    except FileNotFoundError:
        print(f"Error: File {jsonl_path} not found.")
        return

    # 2. Sheet 1: Summary Data (Top-level fields)
    cols_sheet1 = ["date", "total_box_office", "audience_count", "event_count", "average_price"]
    df1 = pd.DataFrame(data, columns=cols_sheet1)

    # 3. Sheet 2: OCR Details (Flattened)
    # Explodes nested dictionaries (e.g., 'ocr_chart_boxoffice') into rows
    sheet2_rows = []
    ocr_targets = ["ocr_chart_boxoffice", "ocr_chart_audience", "ocr_chart_event"]

    for entry in data:
        row_date = entry.get("date")
        for ocr_type in ocr_targets:
            ocr_content = entry.get(ocr_type, {})
            
            if isinstance(ocr_content, dict):
                for category, value in ocr_content.items():
                    sheet2_rows.append({
                        "date": row_date,
                        "ocr_type": ocr_type,  # e.g., ocr_chart_boxoffice
                        "category": category,  # e.g., 音乐
                        "value": value         # e.g., 1.12亿
                    })

    df2 = pd.DataFrame(sheet2_rows)

    # 4. Sheet 3: Province Details (Flattened)
    # Similar to Sheet 2, this now explodes 'province_box' etc. into rows
    sheet3_rows = []
    province_targets = ["province_box", "province_audience", "province_event"]

    for entry in data:
        row_date = entry.get("date")
        for prov_type in province_targets:
            prov_content = entry.get(prov_type, {})
            
            if isinstance(prov_content, dict):
                for province_rank, value in prov_content.items():
                    sheet3_rows.append({
                        "date": row_date,
                        "province_type": prov_type,  # e.g., province_box
                        "province": province_rank,   # e.g., 1. 广西壮族自治区
                        "data_value": value          # e.g., ,22.7%,2732.1万
                    })

    df3 = pd.DataFrame(sheet3_rows)

    # 5. Write to Excel
    with pd.ExcelWriter(excel_path, engine='openpyxl') as writer:
        df1.to_excel(writer, sheet_name="Summary", index=False)
        df2.to_excel(writer, sheet_name="OCR_Details", index=False)
        df3.to_excel(writer, sheet_name="Province_Details", index=False)

    print(f"Success! Data saved to {output_file}")
    print(f"- Sheet 1 rows: {len(df1)}")
    print(f"- Sheet 2 rows: {len(df2)}")
    print(f"- Sheet 3 rows: {len(df3)}")

if __name__ == "__main__":
    process_data(input_file, output_file)