import pandas as pd
from typing import List
import os





def save_to_excel(data: List[dict], OUTPUT_FILE):
    df = pd.DataFrame(data)
    if os.path.exists(OUTPUT_FILE):
        existing_df = pd.read_excel(OUTPUT_FILE)
        df = pd.concat([existing_df, df], ignore_index=True)
    df.to_excel(OUTPUT_FILE, index=False)


def log_failed_tile(tile, FAILED_TILES_LOG):
    with open(FAILED_TILES_LOG, "a") as f:
        f.write(f"{tile}\n")

