import sqlite3
import json
import logging
from datetime import datetime

logger = logging.getLogger(__name__)

DB_PATH = 'stores.db'

def get_connection():
    return sqlite3.connect(DB_PATH)

def init_db():
    """Initializes the SQLite database with required tables."""
    conn = get_connection()
    cursor = conn.cursor()
    
    # Create scrapes table to track each scraping event
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS scrapes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Create stores table to store individual store records linked to a scrape
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS stores (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            scrape_id INTEGER,
            store_name TEXT,
            address TEXT,
            is_robo_shop BOOLEAN,
            raw_html TEXT,
            region TEXT,
            country TEXT,
            date_of_scrap TEXT,
            FOREIGN KEY (scrape_id) REFERENCES scrapes (id)
        )
    ''')
    
    # Safely alter table for existing installations
    try:
        cursor.execute("ALTER TABLE stores ADD COLUMN region TEXT;")
    except sqlite3.OperationalError:
        pass # Column already exists
    try:
        cursor.execute("ALTER TABLE stores ADD COLUMN country TEXT;")
    except sqlite3.OperationalError:
        pass # Column already exists
    try:
        cursor.execute("ALTER TABLE stores ADD COLUMN date_of_scrap TEXT;")
    except sqlite3.OperationalError:
        pass # Column already exists
        
    conn.commit()
    conn.close()
    logger.info("Database initialized successfully.")

def is_robo_shop(store_name: str) -> bool:
    """Helper to determine if a store is a ROBO SHOP based on its name."""
    return "ROBO SHOP" in store_name.upper()

def insert_scrape(stores_data: list) -> int:
    """Inserts a new scrape event and its corresponding stores. Returns the scrape_id."""
    conn = get_connection()
    cursor = conn.cursor()
    
    cursor.execute('INSERT INTO scrapes (timestamp) VALUES (?)', (datetime.now(),))
    scrape_id = cursor.lastrowid
    
    for store in stores_data:
        # Assuming the scraper returns dicts like: {"text": "Store Name\nAddress\n...", "html": "..."}
        lines = store['text'].split('\n')
        store_name = lines[0].strip() if len(lines) > 0 else "Unknown Name"
        
        # Determine address roughly (just skip "Tel:" or "MON" etc)
        address = ""
        if len(lines) > 1:
            if not lines[1].startswith("Tel:") and not lines[1].startswith("MON") and not lines[1].startswith("Store IG:"):
                 address = lines[1].strip()
        
        robo_shop_flag = is_robo_shop(store_name)
        
        cursor.execute('''
            INSERT INTO stores (scrape_id, store_name, address, is_robo_shop, raw_html, region, country, date_of_scrap)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            scrape_id, store_name, address, robo_shop_flag, 
            store.get('html', ''), store.get('region', ''), 
            store.get('country', ''), store.get('date_of_scrap', '')
        ))
        
    conn.commit()
    conn.close()
    logger.info(f"Inserted scrape event {scrape_id} with {len(stores_data)} stores.")
    return scrape_id

def get_last_two_scrapes():
    """Retrieves the store lists from the two most recent scrapes for comparison.
    Returns a tuple: (list_of_current_stores, list_of_previous_stores).
    If there aren't enough scrapes, returns empty lists where applicable.
    Each store in the list is a dict: {'store_name': ..., 'address': ..., 'is_robo_shop': ...}
    """
    conn = get_connection()
    conn.row_factory = sqlite3.Row  # To return dict-like objects
    cursor = conn.cursor()
    
    # Get the IDs of the two most recent scrapes
    cursor.execute('SELECT id FROM scrapes ORDER BY timestamp DESC LIMIT 2')
    recent_scrapes = cursor.fetchall()
    
    current_stores = []
    previous_stores = []
    
    if len(recent_scrapes) >= 1:
        curr_scrape_id = recent_scrapes[0]['id']
        cursor.execute('SELECT store_name, address, is_robo_shop, region, country FROM stores WHERE scrape_id = ?', (curr_scrape_id,))
        current_stores = [dict(row) for row in cursor.fetchall()]
        
    if len(recent_scrapes) >= 2:
        prev_scrape_id = recent_scrapes[1]['id']
        cursor.execute('SELECT store_name, address, is_robo_shop, region, country FROM stores WHERE scrape_id = ?', (prev_scrape_id,))
        previous_stores = [dict(row) for row in cursor.fetchall()]

    conn.close()
    return current_stores, previous_stores

def get_scrape_by_date(date_str: str):
    """(Optional) Pulls out the list of stores for a given date (YYYY-MM-DD)."""
    # ... Optional implementation if required.
    pass

if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    init_db()
