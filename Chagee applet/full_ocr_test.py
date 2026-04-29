import os
from scraping_logic import ChageeOCRExtractor

def test_ocr():
    extractor = ChageeOCRExtractor()
    project_dir = os.path.dirname(os.path.abspath(__file__))
    image_path = os.path.join(project_dir, "temp_scrape.png")
    
    print(f"Testing OCR on: {image_path}")
    if not os.path.exists(image_path):
        print("Image does not exist!")
        return

    results, debug_log = extractor.extract_data(image_path)
    
    print("\n--- OCR DEBUG LOG ---")
    for line in debug_log:
        print(line)
        
    print("\n--- FINAL EXTRACTED RESULTS ---")
    for i, res in enumerate(results):
        print(f"Store {i+1}:")
        print(f"  Name: {res.get('store_name')}")
        print(f"  Status: {res.get('order_status')}")
        print(f"  Cups: {res.get('cup_count')}")
        print("-" * 30)

if __name__ == "__main__":
    test_ocr()
