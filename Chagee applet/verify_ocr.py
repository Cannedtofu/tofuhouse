import os
import json
from ocr_extractor import ChageeOCRExtractor

def load_ground_truth(path):
    truth = {}
    with open(path, 'r', encoding='utf-8') as f:
        for line in f:
            parts = line.strip().split(';')
            if len(parts) == 3:
                img_name, store, status = parts
                if img_name not in truth:
                    truth[img_name] = []
                truth[img_name].append({
                    "store_name": store,
                    "order_status": status
                })
    return truth

def normalize(text):
    if not text: return ""
    # Remove punctuation and whitespace
    return "".join([c for c in text if '\u4e00' <= c <= '\u9fff' or c.isalnum()])

def verify_ocr():
    extractor = ChageeOCRExtractor()
    base_path = "d:/代码项目/Chagee applet/OCR_sample/"
    ground_truth = load_ground_truth(os.path.join(base_path, "test_results.txt"))
    
    total_expected = 0
    total_found = 0
    correct_stores = 0
    correct_status = 0
    
    for img_name in sorted(ground_truth.keys()):
        img_path = os.path.join(base_path, f"{img_name}.png")
        print(f"\nProcessing {img_name}.png...")
        
        expected = ground_truth[img_name]
        total_expected += len(expected)
        
        results = extractor.extract_data(img_path)
        total_found += len(results)
        
        print(f"Expected: {len(expected)}, Found: {len(results)}")
        
        # Order-insensitive matching
        matched_results = []
        found_indices = set()
        
        from difflib import SequenceMatcher
        def fuzzy_ratio(a, b):
            return SequenceMatcher(None, a, b).ratio()

        for exp in expected:
            best_match_idx = -1
            best_score = -1
            
            exp_s = normalize(exp['store_name'])
            exp_o = normalize(exp['order_status'])
            
            for j, res in enumerate(results):
                if j in found_indices: continue
                
                res_s = normalize(res['store_name'])
                res_o = normalize(res['order_status'])
                
                # Use store name similarity as the primary matching criteria
                score = fuzzy_ratio(exp_s, res_s)
                if score > best_score:
                    best_score = score
                    best_match_idx = j
            
            if best_match_idx != -1 and best_score > 0.4: # Found a potential match
                found_indices.add(best_match_idx)
                res = results[best_match_idx]
                res_s = normalize(res['store_name'])
                res_o = normalize(res['order_status'])
                
                store_match = best_score > 0.6 or exp_s in res_s or res_s in exp_s
                status_match = exp_o in res_o or res_o in exp_o or fuzzy_ratio(exp_o, res_o) > 0.8
                
                if store_match: correct_stores += 1
                if status_match: correct_status += 1
                
                print(f"  Exp: '{exp['store_name']}' | '{exp['order_status']}'")
                print(f"  Res: '{res['store_name']}' | '{res['order_status']}'")
                print(f"  Match: Store={store_match} ({best_score:.2f}), Status={status_match}")
            else:
                print(f"  MISSING result for: {exp['store_name']}")

        # Optional: Print extra results
        for j, res in enumerate(results):
            if j not in found_indices:
                print(f"  EXTRA result: '{res['store_name']}' | '{res['order_status']}'")

    print("\n--- FINAL RESULTS ---")
    print(f"Total Expected: {total_expected}")
    print(f"Total Found: {total_found}")
    print(f"Store Name Accuracy: {correct_stores}/{total_expected} ({correct_stores/total_expected:.1%})")
    print(f"Order Status Accuracy: {correct_status}/{total_expected} ({correct_status/total_expected:.1%})")

if __name__ == "__main__":
    verify_ocr()
