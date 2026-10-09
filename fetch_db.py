"""
Deprecated: fetch_db.py
Delegates execution to update_db.py to enforce the schema required by main.py.
"""

from update_db import fetch_multilingual_database

if __name__ == "__main__":
    print("[Notice] Delegating fetch_db execution to update_db pipeline...")
    fetch_multilingual_database()