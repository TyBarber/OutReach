import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from outreach.models import (
    COMPANY_STATUSES,
    CONTACT_TYPES,
    Company,
    CompanyStatus,
)


DATABASE_PATH = Path("data") / "outreach.db"
_UNSET = object()


class DuplicateCompanyError(ValueError):
    """Raised when a company conflicts with an existing company."""


@dataclass(frozen=True)
class ImportSummary:
    imported: int = 0
    duplicates: int = 0
    invalid: int = 0


@dataclass(frozen=True)
class IngestionHistory:
    id: int
    source_name: str
    source_identifier: str | None
    started_at: datetime
    completed_at: datetime | None
    processed: int
    imported: int
    duplicates: int
    filtered: int
    invalid: int


def normalize_domain(value: str | None) -> str | None:
    """Return a lowercase hostname without www, port, path, or query data."""
    if not value or not value.strip():
        return None
    candidate = value.strip()
    parsed = urlsplit(candidate if "://" in candidate else f"//{candidate}")
    hostname = parsed.hostname
    if not hostname:
        return None
    hostname = hostname.rstrip(".").lower()
    if hostname.startswith("www."):
        hostname = hostname[4:]
    try:
        hostname = hostname.encode("idna").decode("ascii")
    except UnicodeError:
        return None
    if not re.fullmatch(
        r"(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+"
        r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?",
        hostname,
    ):
        return None
    return hostname


def normalize_company_name(value: str) -> str:
    """Normalize capitalization and whitespace for conservative matching."""
    return re.sub(r"\s+", " ", value).strip().casefold()


def _format_timestamp(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds").replace(
        "+00:00", "Z"
    )


def _parse_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def get_connection(database_path: Path = DATABASE_PATH) -> sqlite3.Connection:
    database_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(database_path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def _create_companies_table(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS companies (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            normalized_name TEXT NOT NULL,
            domain TEXT,
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


def _create_ingestion_tables(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS company_sources (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            company_id INTEGER NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
            source_name TEXT NOT NULL,
            source_identifier TEXT NOT NULL DEFAULT '',
            discovered_at TEXT NOT NULL DEFAULT (
                strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
            ),
            UNIQUE(company_id, source_name, source_identifier)
        )
        """
    )


def _create_contact_tables(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS contact_routes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            company_id INTEGER NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
            route_type TEXT NOT NULL,
            value TEXT NOT NULL COLLATE NOCASE,
            source_url TEXT,
            source_type TEXT NOT NULL DEFAULT 'public_website',
            confidence REAL NOT NULL DEFAULT 0.5,
            verified INTEGER NOT NULL DEFAULT 0,
            discovered_at TEXT NOT NULL,
            last_checked_at TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'active',
            notes TEXT,
            UNIQUE(company_id, route_type, value)
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS contact_discovery_state (
            company_id INTEGER PRIMARY KEY REFERENCES companies(id) ON DELETE CASCADE,
            last_discovery_at TEXT NOT NULL,
            discovery_status TEXT NOT NULL,
            pages_checked INTEGER NOT NULL DEFAULT 0,
            routes_found INTEGER NOT NULL DEFAULT 0,
            error_message TEXT
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS ingestion_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_name TEXT NOT NULL,
            source_identifier TEXT,
            started_at TEXT NOT NULL,
            completed_at TEXT,
            processed INTEGER NOT NULL DEFAULT 0,
            imported INTEGER NOT NULL DEFAULT 0,
            duplicates INTEGER NOT NULL DEFAULT 0,
            filtered INTEGER NOT NULL DEFAULT 0,
            invalid INTEGER NOT NULL DEFAULT 0
        )
        """
    )


def _migrate_legacy_table(connection: sqlite3.Connection) -> None:
    columns = {
        row["name"]: row for row in connection.execute("PRAGMA table_info(companies)")
    }
    if not columns or (
        "normalized_name" in columns and columns["domain"]["notnull"] == 0
    ):
        return

    rows = connection.execute("SELECT * FROM companies ORDER BY id").fetchall()
    connection.execute("ALTER TABLE companies RENAME TO companies_legacy")
    _create_companies_table(connection)
    for row in rows:
        domain = normalize_domain(row["domain"] or row["website"])
        connection.execute(
            """
            INSERT INTO companies (
                id, name, normalized_name, domain, website, careers_url,
                contact_email, contact_type, status, last_contacted_at,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                row["id"], row["name"], normalize_company_name(row["name"]),
                domain, row["website"], row["careers_url"], row["contact_email"],
                row["contact_type"], row["status"], row["last_contacted_at"],
                row["created_at"], row["updated_at"],
            ),
        )
    connection.execute("DROP TABLE companies_legacy")


def initialize_database(database_path: Path = DATABASE_PATH) -> Path:
    connection = get_connection(database_path)
    try:
        _create_companies_table(connection)
        _migrate_legacy_table(connection)
        _create_ingestion_tables(connection)
        _create_contact_tables(connection)
        connection.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS ux_companies_domain "
            "ON companies(domain) WHERE domain IS NOT NULL"
        )
        connection.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS ux_companies_name_no_domain "
            "ON companies(normalized_name) WHERE domain IS NULL"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS ix_companies_status ON companies(status)"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS ix_company_sources_company "
            "ON company_sources(company_id)"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS ix_ingestion_history_source "
            "ON ingestion_history(source_name)"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS ix_contact_routes_company_status "
            "ON contact_routes(company_id, status)"
        )
        connection.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS ux_contact_routes_company_value "
            "ON contact_routes(company_id, value)"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS ix_discovery_state_status "
            "ON contact_discovery_state(discovery_status, last_discovery_at)"
        )
        connection.commit()
    finally:
        connection.close()
    return database_path


def _row_to_company(row: sqlite3.Row) -> Company:
    return Company(
        id=row["id"],
        name=row["name"],
        domain=row["domain"],
        website=row["website"],
        careers_url=row["careers_url"],
        contact_email=row["contact_email"],
        contact_type=row["contact_type"],
        status=row["status"],
        last_contacted_at=_parse_timestamp(row["last_contacted_at"]),
        created_at=_parse_timestamp(row["created_at"]),
        updated_at=_parse_timestamp(row["updated_at"]),
    )


def add_company(company: Company, database_path: Path = DATABASE_PATH) -> int:
    initialize_database(database_path)
    name = company.name.strip()
    if not name:
        raise ValueError("Company name is required.")
    domain = normalize_domain(company.domain or company.website)
    status = company.status or "discovered"
    if status not in COMPANY_STATUSES:
        raise ValueError(f"Unknown company status: {status}")
    if company.contact_type and company.contact_type not in CONTACT_TYPES:
        raise ValueError(f"Unknown contact type: {company.contact_type}")

    connection = get_connection(database_path)
    try:
        connection.execute("BEGIN IMMEDIATE")
        if domain is None:
            duplicate = connection.execute(
                "SELECT id FROM companies WHERE normalized_name = ? LIMIT 1",
                (normalize_company_name(name),),
            ).fetchone()
            if duplicate:
                raise DuplicateCompanyError(
                    f"A company already exists for domain or name: {name}"
                )
        cursor = connection.execute(
            """
            INSERT INTO companies (
                name, normalized_name, domain, website, careers_url,
                contact_email, contact_type, status, last_contacted_at,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                name, normalize_company_name(name), domain, company.website,
                company.careers_url, company.contact_email, company.contact_type,
                status, _format_timestamp(company.last_contacted_at),
                _format_timestamp(company.created_at),
                _format_timestamp(company.updated_at),
            ),
        )
        connection.commit()
        if cursor.lastrowid is None:
            raise RuntimeError("SQLite did not return a company ID.")
        return cursor.lastrowid
    except DuplicateCompanyError:
        connection.rollback()
        raise
    except sqlite3.IntegrityError as error:
        connection.rollback()
        raise DuplicateCompanyError(
            f"A company already exists for domain or name: {name}"
        ) from error
    finally:
        connection.close()


def get_company(company_id: int, database_path: Path = DATABASE_PATH) -> Company | None:
    initialize_database(database_path)
    connection = get_connection(database_path)
    try:
        row = connection.execute(
            "SELECT * FROM companies WHERE id = ?", (company_id,)
        ).fetchone()
        return _row_to_company(row) if row else None
    finally:
        connection.close()


def find_company_id(
    company: Company, database_path: Path = DATABASE_PATH
) -> int | None:
    initialize_database(database_path)
    domain = normalize_domain(company.domain or company.website)
    connection = get_connection(database_path)
    try:
        if domain:
            row = connection.execute(
                "SELECT id FROM companies WHERE domain = ?", (domain,)
            ).fetchone()
        else:
            row = connection.execute(
                "SELECT id FROM companies WHERE normalized_name = ? LIMIT 1",
                (normalize_company_name(company.name),),
            ).fetchone()
        return int(row["id"]) if row else None
    finally:
        connection.close()


def add_company_source(
    company_id: int,
    source_name: str,
    source_identifier: str | None = None,
    database_path: Path = DATABASE_PATH,
) -> None:
    initialize_database(database_path)
    connection = get_connection(database_path)
    try:
        connection.execute(
            """
            INSERT OR IGNORE INTO company_sources (
                company_id, source_name, source_identifier
            ) VALUES (?, ?, ?)
            """,
            (company_id, source_name, source_identifier or ""),
        )
        connection.commit()
    finally:
        connection.close()


def list_company_sources(
    company_id: int, database_path: Path = DATABASE_PATH
) -> list[dict[str, Any]]:
    initialize_database(database_path)
    connection = get_connection(database_path)
    try:
        rows = connection.execute(
            """
            SELECT source_name, source_identifier, discovered_at
            FROM company_sources
            WHERE company_id = ?
            ORDER BY id
            """,
            (company_id,),
        ).fetchall()
        return [
            {
                "source_name": row["source_name"],
                "source_identifier": row["source_identifier"] or None,
                "discovered_at": _parse_timestamp(row["discovered_at"]),
            }
            for row in rows
        ]
    finally:
        connection.close()


def start_ingestion(
    source_name: str,
    source_identifier: str | None = None,
    database_path: Path = DATABASE_PATH,
) -> int:
    initialize_database(database_path)
    connection = get_connection(database_path)
    try:
        cursor = connection.execute(
            """
            INSERT INTO ingestion_history (
                source_name, source_identifier, started_at
            ) VALUES (?, ?, ?)
            """,
            (
                source_name,
                source_identifier,
                _format_timestamp(datetime.now(timezone.utc)),
            ),
        )
        connection.commit()
        if cursor.lastrowid is None:
            raise RuntimeError("SQLite did not return an ingestion ID.")
        return cursor.lastrowid
    finally:
        connection.close()


def complete_ingestion(
    ingestion_id: int,
    *,
    processed: int,
    imported: int,
    duplicates: int,
    filtered: int,
    invalid: int,
    database_path: Path = DATABASE_PATH,
) -> None:
    connection = get_connection(database_path)
    try:
        connection.execute(
            """
            UPDATE ingestion_history
            SET completed_at = ?, processed = ?, imported = ?,
                duplicates = ?, filtered = ?, invalid = ?
            WHERE id = ?
            """,
            (
                _format_timestamp(datetime.now(timezone.utc)),
                processed,
                imported,
                duplicates,
                filtered,
                invalid,
                ingestion_id,
            ),
        )
        connection.commit()
    finally:
        connection.close()


def list_ingestion_history(
    database_path: Path = DATABASE_PATH,
) -> list[IngestionHistory]:
    initialize_database(database_path)
    connection = get_connection(database_path)
    try:
        rows = connection.execute(
            "SELECT * FROM ingestion_history ORDER BY id DESC"
        ).fetchall()
        return [
            IngestionHistory(
                id=row["id"],
                source_name=row["source_name"],
                source_identifier=row["source_identifier"],
                started_at=_parse_timestamp(row["started_at"]),
                completed_at=_parse_timestamp(row["completed_at"]),
                processed=row["processed"],
                imported=row["imported"],
                duplicates=row["duplicates"],
                filtered=row["filtered"],
                invalid=row["invalid"],
            )
            for row in rows
        ]
    finally:
        connection.close()


def list_companies(
    *,
    status: CompanyStatus | None = None,
    has_contact: bool | None = None,
    database_path: Path = DATABASE_PATH,
) -> list[Company]:
    initialize_database(database_path)
    conditions: list[str] = []
    parameters: list[Any] = []
    if status is not None:
        conditions.append("status = ?")
        parameters.append(status)
    if has_contact is True:
        conditions.append(
            "((contact_email IS NOT NULL AND trim(contact_email) <> '') "
            "OR EXISTS (SELECT 1 FROM contact_routes "
            "WHERE contact_routes.company_id = companies.id "
            "AND contact_routes.status = 'active'))"
        )
    elif has_contact is False:
        conditions.append(
            "(contact_email IS NULL OR trim(contact_email) = '') "
            "AND NOT EXISTS (SELECT 1 FROM contact_routes "
            "WHERE contact_routes.company_id = companies.id "
            "AND contact_routes.status = 'active')"
        )
    where = f" WHERE {' AND '.join(conditions)}" if conditions else ""
    connection = get_connection(database_path)
    try:
        rows = connection.execute(
            f"SELECT * FROM companies{where} ORDER BY id", parameters
        ).fetchall()
        return [_row_to_company(row) for row in rows]
    finally:
        connection.close()


def update_company(
    company_id: int,
    *,
    website: str | None | object = _UNSET,
    domain: str | None | object = _UNSET,
    careers_url: str | None | object = _UNSET,
    contact_email: str | None | object = _UNSET,
    contact_type: str | None | object = _UNSET,
    status: str | object = _UNSET,
    last_contacted_at: datetime | None | object = _UNSET,
    database_path: Path = DATABASE_PATH,
) -> bool:
    initialize_database(database_path)
    updates: list[str] = []
    values: list[Any] = []
    fields = {
        "website": website,
        "careers_url": careers_url,
        "contact_email": contact_email,
    }
    for column, value in fields.items():
        if value is not _UNSET:
            updates.append(f"{column} = ?")
            values.append(value)
    if domain is not _UNSET:
        updates.append("domain = ?")
        values.append(normalize_domain(domain if isinstance(domain, str) else None))
    if contact_type is not _UNSET:
        if contact_type is not None and contact_type not in CONTACT_TYPES:
            raise ValueError(f"Unknown contact type: {contact_type}")
        updates.append("contact_type = ?")
        values.append(contact_type)
    if status is not _UNSET:
        if status not in COMPANY_STATUSES:
            raise ValueError(f"Unknown company status: {status}")
        updates.append("status = ?")
        values.append(status)
    if last_contacted_at is not _UNSET:
        updates.append("last_contacted_at = ?")
        values.append(
            _format_timestamp(
                last_contacted_at if isinstance(last_contacted_at, datetime) else None
            )
        )
    if not updates:
        return get_company(company_id, database_path) is not None

    updates.append("updated_at = ?")
    values.append(_format_timestamp(datetime.now(timezone.utc)))
    values.append(company_id)
    connection = get_connection(database_path)
    try:
        cursor = connection.execute(
            f"UPDATE companies SET {', '.join(updates)} WHERE id = ?", values
        )
        connection.commit()
        return cursor.rowcount > 0
    except sqlite3.IntegrityError as error:
        connection.rollback()
        raise DuplicateCompanyError("The updated domain is already in use.") from error
    finally:
        connection.close()


def set_company_status(
    company_id: int,
    status: CompanyStatus,
    database_path: Path = DATABASE_PATH,
) -> bool:
    if status == "sent":
        return update_company(
            company_id,
            status=status,
            last_contacted_at=datetime.now(timezone.utc),
            database_path=database_path,
        )
    return update_company(company_id, status=status, database_path=database_path)


def get_outreach_queue(database_path: Path = DATABASE_PATH) -> list[Company]:
    return list_companies(status="queued", database_path=database_path)


def get_stats(database_path: Path = DATABASE_PATH) -> dict[str, int]:
    initialize_database(database_path)
    connection = get_connection(database_path)
    try:
        row = connection.execute(
            """
            SELECT
                count(*) AS total,
                sum(status = 'discovered') AS discovered,
                sum(
                    (contact_email IS NOT NULL AND trim(contact_email) <> '')
                    OR EXISTS (
                        SELECT 1 FROM contact_routes
                        WHERE contact_routes.company_id = companies.id
                          AND contact_routes.status = 'active'
                    )
                ) AS with_contacts,
                sum(
                    (contact_email IS NULL OR trim(contact_email) = '')
                    AND NOT EXISTS (
                        SELECT 1 FROM contact_routes
                        WHERE contact_routes.company_id = companies.id
                          AND contact_routes.status = 'active'
                    )
                ) AS without_contacts,
                sum(status = 'queued') AS queued,
                sum(status = 'sent') AS sent,
                sum(status = 'replied') AS replied,
                sum(status = 'failed') AS failed,
                sum(status = 'do_not_contact') AS do_not_contact,
                (SELECT count(*) FROM company_sources) AS source_records,
                (SELECT count(*) FROM ingestion_history) AS ingestion_runs
            FROM companies
            """
        ).fetchone()
        return {key: int(row[key] or 0) for key in row.keys()}
    finally:
        connection.close()


def import_companies_csv(
    csv_path: Path,
    database_path: Path = DATABASE_PATH,
) -> ImportSummary:
    from outreach.ingestion import CSVCompanySource, ingest_source

    summary = ingest_source(CSVCompanySource(csv_path), database_path=database_path)
    return ImportSummary(summary.imported, summary.duplicates, summary.invalid)
