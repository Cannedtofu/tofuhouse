from ocr_extractor import ChageeOCRExtractor
import json

def list_ocr_centers():
    ex = ChageeOCRExtractor()
    res = ex.ocr_full_image('inspect_city.png')
    for box, (text, score) in res:
        cx = (box[0][0] + box[1][0]) / 2
        cy = (box[0][1] + box[2][1]) / 2
        print(f"Text: '{text}' | Center: ({cx:.1f}, {cy:.1f})")

if __name__ == "__main__":
    list_ocr_centers()
