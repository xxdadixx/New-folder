"""
Deprecated: scrape_qa.py
All web scraping operations have been consolidated into update_db.py.
"""

from update_db import fetch_multilingual_database

if __name__ == "__main__":
    print("[Notice] Delegating scrape_qa execution to update_db pipeline...")
    fetch_multilingual_database()