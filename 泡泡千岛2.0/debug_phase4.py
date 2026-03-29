import process_skus
import logging
import sys

# Setup logging to console
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)

if __name__ == "__main__":
    print("Starting Phase 4 Debugger...")
    print("This will run process_skus.py directly using results.db as input.")
    
    try:
        total, success = process_skus.main()
        print(f"\nFinal Summary: Processed {success} out of {total} series.")
    except Exception as e:
        logging.exception("Phase 4 failed with a fatal error:")
