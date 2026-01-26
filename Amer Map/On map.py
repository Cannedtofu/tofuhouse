import os
import sys
import json
import pandas as pd

# Step 0: Set working directory to script's folder
script_dir = os.path.dirname(os.path.abspath(sys.argv[0]))
os.chdir(script_dir)
print("📂 Working directory set to:", os.getcwd())

# File paths
input_txt_file = 'on stores.txt'
output_excel_file = 'output.xlsx'

# Step 1: Read JSON string from text file
try:
    with open(input_txt_file, 'r', encoding='utf-8') as f:
        json_str = f.read()
except FileNotFoundError:
    print(f"❌ File not found: {input_txt_file}")
    sys.exit(1)

# Step 2: Parse JSON string
try:
    data = json.loads(json_str)
except json.JSONDecodeError as e:
    print("❌ Error parsing JSON:", e)
    sys.exit(1)

# Step 3: Extract 'dealers' list
if not isinstance(data, dict) or 'dealers' not in data:
    print("❌ JSON does not contain 'dealers' list.")
    sys.exit(1)

dealers = data['dealers']
print(f"✅ Found {len(dealers)} dealers in JSON data.")


if not isinstance(dealers, list) or not all(isinstance(item, dict) for item in dealers):
    print("❌ 'dealers' must be a list of dictionaries.")
    sys.exit(1)


# Step 4: Write to Excel using pandas
try:
    df = pd.DataFrame(dealers)
    print(f"✅ DataFrame created with {len(df)} rows and {len(df.columns)} columns.")
    
    df.to_excel(output_excel_file, index=False)
    print(f"✅ Excel file written to {output_excel_file}")
except Exception as e:
    print(f"❌ Error writing Excel file: {e}")
