import sqlite3
from datetime import timezone

import pytest

from outreach.database import (
    DuplicateCompanyError,
    add_company,
    get_company,
    get_outreach_queue,
    get_stats,
    initialize_database,
    list_companies,
    normalize_domain,
    set_company_status,
    update_company,
)
from outreach.models import Company


def test_initialization_is_repeatable(tmp_path):
    database = tmp_path / "data" / "outreach.db"
    assert initialize_database(database) == database
    assert initialize_database(database) == database
    assert database.is_file()


def test_initialization_migrates_legacy_table_without_losing_rows(tmp_path):
    database = tmp_path / "outreach.db"
    connection = sqlite3.connect(database)
    connection.execute(
        """
        CREATE TABLE companies (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            domain TEXT NOT NULL,
            website TEXT,
            careers_url TEXT,
            contact_email TEXT,
            contact_type TEXT,
            status TEXT NOT NULL DEFAULT 'discovered',
            last_contacted_at TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    connection.execute(
        """
        INSERT INTO companies (
            name, domain, status, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?)
        """,
        (
            "Existing Company",
            "https://www.example.com/jobs",
            "discovered",
            "2026-01-01T00:00:00.000000Z",
            "2026-01-01T00:00:00.000000Z",
        ),
    )
    connection.commit()
    connection.close()

    initialize_database(database)

    company = get_company(1, database)
    assert company is not None
    assert company.name == "Existing Company"
    assert company.domain == "example.com"


@pytest.mark.parametrize(
    "value",
    [
        "https://www.example.com/",
        "http://example.com",
        "https://example.com/jobs?q=1#openings",
        "www.example.com",
        "EXAMPLE.COM",
    ],
)
def test_domain_normalization(value):
    assert normalize_domain(value) == "example.com"


def test_add_get_and_list_company(tmp_path):
    database = tmp_path / "outreach.db"
    company_id = add_company(
        Company(
            name="Cloudflare",
            domain="https://www.cloudflare.com/jobs",
            contact_email="careers@cloudflare.com",
            contact_type="careers",
        ),
        database,
    )
    assert isinstance(company_id, int)
    company = get_company(company_id, database)
    assert company is not None
    assert company.domain == "cloudflare.com"
    assert company.status == "discovered"
    assert company.created_at.tzinfo == timezone.utc
    assert [item.id for item in list_companies(database_path=database)] == [company_id]
    assert len(list_companies(has_contact=True, database_path=database)) == 1
    assert list_companies(has_contact=False, database_path=database) == []


def test_duplicate_domain_is_rejected(tmp_path):
    database = tmp_path / "outreach.db"
    add_company(Company("Cloudflare", "https://www.cloudflare.com"), database)
    with pytest.raises(DuplicateCompanyError):
        add_company(Company("Cloudflare Inc", "cloudflare.com"), database)


def test_duplicate_name_without_domain_is_rejected(tmp_path):
    database = tmp_path / "outreach.db"
    add_company(Company("  Example   Company  "), database)
    with pytest.raises(DuplicateCompanyError):
        add_company(Company("example company"), database)


def test_domainless_company_matches_existing_name_with_domain(tmp_path):
    database = tmp_path / "outreach.db"
    add_company(Company("Cloudflare", "cloudflare.com"), database)
    with pytest.raises(DuplicateCompanyError):
        add_company(Company("  CLOUDFLARE "), database)


def test_update_contact_and_domain(tmp_path):
    database = tmp_path / "outreach.db"
    company_id = add_company(Company("Example"), database)
    assert update_company(
        company_id,
        domain="HTTPS://WWW.EXAMPLE.COM/jobs",
        website="https://example.com",
        careers_url="https://example.com/jobs",
        contact_email="jobs@example.com",
        contact_type="careers",
        database_path=database,
    )
    company = get_company(company_id, database)
    assert company.domain == "example.com"
    assert company.contact_email == "jobs@example.com"
    assert company.contact_type == "careers"


def test_queue_statuses_and_sent_timestamp(tmp_path):
    database = tmp_path / "outreach.db"
    queued_id = add_company(Company("Queued"), database)
    blocked_id = add_company(Company("Blocked"), database)
    assert set_company_status(queued_id, "queued", database)
    assert set_company_status(blocked_id, "do_not_contact", database)
    assert [company.id for company in get_outreach_queue(database)] == [queued_id]
    assert set_company_status(queued_id, "sent", database)
    sent = get_company(queued_id, database)
    assert sent.status == "sent"
    assert sent.last_contacted_at is not None
    assert get_outreach_queue(database) == []


def test_stats(tmp_path):
    database = tmp_path / "outreach.db"
    first = add_company(Company("First", contact_email="hello@first.test"), database)
    second = add_company(Company("Second"), database)
    set_company_status(first, "queued", database)
    set_company_status(second, "failed", database)
    stats = get_stats(database)
    assert stats == {
        "total": 2,
        "discovered": 0,
        "with_contacts": 1,
        "without_contacts": 1,
        "queued": 1,
        "sent": 0,
        "replied": 0,
        "failed": 1,
        "do_not_contact": 0,
        "source_records": 0,
        "ingestion_runs": 0,
    }
