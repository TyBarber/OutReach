import csv
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
    return hostname or None


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
        conditions.append("contact_email IS NOT NULL AND trim(contact_email) <> ''")
    elif has_contact is False:
        conditions.append("(contact_email IS NULL OR trim(contact_email) = '')")
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
                sum(contact_email IS NOT NULL AND trim(contact_email) <> '') AS with_contacts,
                sum(contact_email IS NULL OR trim(contact_email) = '') AS without_contacts,
                sum(status = 'queued') AS queued,
                sum(status = 'sent') AS sent,
                sum(status = 'replied') AS replied,
                sum(status = 'failed') AS failed,
                sum(status = 'do_not_contact') AS do_not_contact
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
    imported = duplicates = invalid = 0
    with csv_path.open(newline="", encoding="utf-8-sig") as file:
        reader = csv.DictReader(file)
        if not reader.fieldnames or "name" not in reader.fieldnames:
            raise ValueError("CSV must contain a 'name' column.")
        for row in reader:
            try:
                name = (row.get("name") or "").strip()
                if not name:
                    raise ValueError("Company name is required.")
                contact_type = (row.get("contact_type") or "").strip() or None
                company = Company(
                    name=name,
                    website=(row.get("website") or "").strip() or None,
                    domain=(row.get("domain") or "").strip() or None,
                    careers_url=(row.get("careers_url") or "").strip() or None,
                    contact_email=(row.get("contact_email") or "").strip() or None,
                    contact_type=contact_type,
                )
                add_company(company, database_path)
                imported += 1
            except DuplicateCompanyError:
                duplicates += 1
            except (TypeError, ValueError):
                invalid += 1
    return ImportSummary(imported, duplicates, invalid)
