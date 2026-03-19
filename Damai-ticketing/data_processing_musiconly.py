import pandas as pd
import json

def transform_music_data(input_file, output_file):
    final_rows = []

    with open(input_file, "r", encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            
            data = json.loads(line)
            
            # 1. Extract base information for this record
            base_info = {
                "date": data.get("date"),
                "total_box_office": data.get("total_box_office"),
                "audience_count": data.get("audience_count"),
                "event_count": data.get("event_count"),
                "average_price": data.get("average_price")
            }

            # 2. Process Music Categories with Flags
            # Mapping of JSON keys to the requested CSV Flags
            music_sections = {
                "music_box": "Box Office",
                "music_audience": "Audience",
                "music_event": "Event"
            }

            for json_key, flag_name in music_sections.items():
                section_data = data.get(json_key, {})
                if isinstance(section_data, dict):
                    for sub_category, value in section_data.items():
                        # Create a new row for every sub-category entry
                        row = base_info.copy()
                        row["Metric_Flag"] = flag_name
                        row["Sub_Category"] = sub_category
                        row["Music_Data_Value"] = value
                        final_rows.append(row)

    # 3. Create DataFrame and export
    df = pd.DataFrame(final_rows)
    # Using utf-8-sig ensures Chinese characters open correctly in Excel
    df.to_csv(output_file, index=False, encoding='utf-8-sig')
    print(f"Successfully processed {len(df)} rows to {output_file}")

# Execution
transform_music_data("scraped_data_music_only.jsonl", "processed_live_entertainment_data_music_only_260305.csv")