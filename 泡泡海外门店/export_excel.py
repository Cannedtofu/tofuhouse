import sqlite3
import pandas as pd
import os

def export_db_to_excel(db_path='stores.db', output_file='stores_export.xlsx'):
    print(f"Connecting to database: {db_path}...")
    
    if not os.path.exists(db_path):
        print(f"Error: Database {db_path} does not exist.")
        return
        
    try:
        # Connect to the SQLite database
        conn = sqlite3.connect(db_path)
        
        # Query all data from the stores table
        query = "SELECT * FROM stores"
        
        # Read the data into a pandas DataFrame
        df = pd.read_sql_query(query, conn)
        
        # Close the connection
        conn.close()
        
        # Export the DataFrame to an Excel file
        print(f"Writing data to {output_file}...")
        df.to_excel(output_file, index=False, engine='openpyxl')
        
        print(f"Successfully exported {len(df)} records to {output_file}!")
        
    except Exception as e:
        print(f"An error occurred: {e}")

if __name__ == "__main__":
    export_db_to_excel()
