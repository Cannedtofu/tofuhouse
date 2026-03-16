import os
import pandas as pd
from datetime import datetime
from ocr_extractor import ChageeOCRExtractor

def export_results_to_excel():
    extractor = ChageeOCRExtractor()
    base_path = "d:/代码项目/Chagee applet/OCR_sample/"
    
    # Get current timestamp info
    now = datetime.now()
    date_str = now.strftime("%Y-%m-%d")
    time_str = now.strftime("%H:%M")
    day_str = now.strftime("%A")
    
    # Get all png files
    image_files = [f for f in os.listdir(base_path) if f.endswith('.png') and f.startswith('test_')]
    image_files.sort()
    
    all_results = []
    
    for img_file in image_files:
        img_path = os.path.join(base_path, img_file)
        print(f"Processing {img_file}...")
        
        results = extractor.extract_data(img_path)
        for res in results:
            all_results.append({
                "Store Name": res['store_name'],
                "Order Status": res['order_status'],
                "Cup Count": res['cup_count'],
                "Date": date_str,
                "Time": time_str,
                "Day": day_str
            })
            
    if all_results:
        df = pd.DataFrame(all_results)
        # Reorder columns as requested
        df = df[["Store Name", "Order Status", "Cup Count", "Date", "Time", "Day"]]
        
        output_file = os.path.join(base_path, "ocr_results.xlsx")
        df.to_excel(output_file, index=False)
        print(f"\nResults successfully exported to {output_file}")
    else:
        print("No results found to export.")

if __name__ == "__main__":
    export_results_to_excel()
