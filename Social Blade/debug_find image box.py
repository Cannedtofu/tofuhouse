import cv2
import numpy as np
from PIL import Image

# Load your saved image
img_path = "debug_mouse_area.png"  # Replace with your file
img = cv2.imread(img_path)
original = img.copy()

gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
height, width = gray.shape

# === Thresholding to isolate UI elements ===
_, thresh = cv2.threshold(gray, 245, 255, cv2.THRESH_BINARY)

cv2.imwrite("thresh_debug.png", thresh)



# # Morphological closing to fill gaps in the tooltip
# kernel = np.ones((3, 3), np.uint8)
# closed = cv2.morphologyEx(thresh, cv2.MORPH_CLOSE, kernel, iterations=2)
# cv2.imwrite("closed_debug.png", closed)

# Find external contours
contours, _ = cv2.findContours(thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
print(f"Found {len(contours)} contours")

candidates = []

#lable and show all candidates
for cnt in contours:
    x, y, w, h = cv2.boundingRect(cnt)
    cv2.drawContours(original, [cnt], -1, (255, 0, 0), 2)  # Blue
    label = f"{x},{y},{w},{h}"

    # Calculate center of bounding box
    cx = x + w // 2
    cy = y + h // 2

    # Get text size to center it
    (text_w, text_h), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.4, 1)
    text_x = cx - text_w // 2
    text_y = cy + text_h // 2

    # Draw the label at the center
    cv2.putText(original, label, (text_x, text_y), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (255, 0, 0), 1)


for cnt in contours:
    x, y, w, h = cv2.boundingRect(cnt)
    area = cv2.contourArea(cnt)
    print(f"Box ({x},{y},{w},{h})")

    # Skip tiny contours
    if area < 500:
        print('area failed')
        continue

    # Skip boxes touching edges
    if x <= 0 or y <= 0 or x + w >= width or y + h >= height:
        print('edge failed')
        continue

    # Evaluate background color in the box
    roi = img[y:y+h, x:x+w]
    mean_color = cv2.mean(roi)[:3]
    print(f"Box ({x},{y},{w},{h}) → mean RGB: {mean_color}")

    # check if close to rectangle
    approx = cv2.approxPolyDP(cnt, 0.02 * cv2.arcLength(cnt, True), True)
    if not (4 <= len(approx) <= 6):
        print('rec failed')
        continue  # Not a quadrilateral
        

    # Tooltip has dark background — all channels should be low
    if not all(channel > 210 for channel in mean_color):
        continue

    # Candidate looks valid
    candidates.append((x, y, w, h))
    print(f"Box ({x},{y},{w},{h}) → looks good")
    cv2.rectangle(original, (x, y), (x + w, y + h), (0, 255, 0), 2)

print(candidates)

# Draw best candidate in red (if any)
if candidates:
    x, y, w, h = max(candidates, key=lambda b: b[2] * b[3])
    cv2.rectangle(original, (x, y), (x + w, y + h), (0, 0, 255), 3)
    print(f"Selected box: ({x},{y},{w},{h})")

# Save and display final debug image
cv2.imwrite("debug_boxes_final.png", original)
cv2.imshow("Tooltip Box Debug", original)
cv2.waitKey(0)
cv2.destroyAllWindows()