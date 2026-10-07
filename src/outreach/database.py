import sqlite3
from pathlib import Path


DATABASE_PATH = Path("data") / "outreach.db"


def get_connection(
    database_path: Path = DATABASE_PATH,
) -> sqlite3.Connection:
    """Open and configure a connection to the SQLite database."""
    connection = sqlite3.connect(database_path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def initialize_database(
    database_path: Path = DATABASE_PATH,
) -> Path:
    """Create the database directory, file, and companies table."""
    database_path.parent.mkdir(parents=True, exist_ok=True)

    connection = get_connection(database_path)

    try:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS companies (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                domain TEXT NOT NULL,
                website TEXT,
                careers_url TEXT,
                contact_email TEXT,
                contact_type TEXT,
                status TEXT NOT NULL DEFAULT 'discovered',
                last_contacted_at TEXT,
                created_at TEXT NOT NULL DEFAULT (
                    strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                ),
                updated_at TEXT NOT NULL DEFAULT (
                    strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                )
            )
            """
        )
        connection.commit()
    finally:
        connection.close()

    return database_path