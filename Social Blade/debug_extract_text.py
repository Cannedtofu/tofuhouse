
from PIL import Image
import pytesseract
import cv2
import numpy as np

pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"

def extract_text_from_image(img: Image.Image) -> str:
    # Convert the PIL image to a numpy array for OpenCV processing
    img_cv = np.array(img)
    
    # Convert the image to grayscale (Tesseract prefers grayscale input)
    gray = cv2.cvtColor(img_cv, cv2.COLOR_RGB2GRAY)
    
    # Apply thresholding to increase contrast between text and background
    _, thresh = cv2.threshold(gray, 200, 255, cv2.THRESH_BINARY_INV)  # Inverse binary threshold

    # Optionally apply denoising (removes small noise pixels)
    denoised = cv2.fastNlMeansDenoising(thresh, None, 30, 7, 21)

    # Convert back to PIL image format (if needed)
    processed_image = Image.fromarray(denoised)
    
    # Extract text using pytesseract
    return pytesseract.image_to_string(processed_image)


def main():
    # Load the image
    img_path = "D:\代码项目\cropped_tooltip.png"  # Path to the image
    try:
        img = Image.open(img_path)
        print(f"Image '{img_path}' successfully opened.")
    except:
        print(f"Failed to open image '{img_path}'. Please check the path.")
        return

    # Extract text from the image
    extracted_text = extract_text_from_image(img)
    
    # Print the extracted texts
    print("Extracted Text:", extracted_text)

if __name__ == "__main__":
    main()
