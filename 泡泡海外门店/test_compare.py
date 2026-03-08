import sqlite3
import random

def mutate_db_for_test():
    """
    Simulates a store closing and a store opening to test the compare logic.
    We will:
    1. Alter the name of one store in the MOST RECENT scrape (simulating a NEW store).
    2. Since the name changed, the original name will be missing from the current scrape
       but present in the PREVIOUS scrape (simulating a CLOSED store).
    """
    conn = sqlite3.connect('stores.db')
    cursor = conn.cursor()
    
    # Get the latest scrape ID
    cursor.execute("SELECT id FROM scrapes ORDER BY timestamp DESC LIMIT 1")
    latest_scrape_id = cursor.fetchone()[0]
    
    # Find a regular store to rename (simulating closed old, opened new)
    cursor.execute("SELECT id, store_name FROM stores WHERE scrape_id = ? AND is_robo_shop = 0 LIMIT 1", (latest_scrape_id,))
    target_store = cursor.fetchone()
    
    if target_store:
        store_id, old_name = target_store
        new_name = old_name + " (NEW TEST LOCATION)"
        cursor.execute("UPDATE stores SET store_name = ? WHERE id = ?", (new_name, store_id))
        print(f"Test mutation: Renamed '{old_name}' to '{new_name}' in scrape {latest_scrape_id}")
    
    # Find a ROBO SHOP to rename to test that path too
    cursor.execute("SELECT id, store_name FROM stores WHERE scrape_id = ? AND is_robo_shop = 1 LIMIT 1", (latest_scrape_id,))
    target_robo = cursor.fetchone()
    
    if target_robo:
        store_id, old_name = target_robo
        new_name = old_name + " (NEW TEST ROBO)"
        cursor.execute("UPDATE stores SET store_name = ? WHERE id = ?", (new_name, store_id))
        print(f"Test mutation: Renamed '{old_name}' to '{new_name}' in scrape {latest_scrape_id}")
        
    conn.commit()
    conn.close()

if __name__ == "__main__":
    mutate_db_for_test()
    print("Database mutated successfully.")
