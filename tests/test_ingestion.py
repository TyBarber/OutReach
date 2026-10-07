import json

from outreach.database import (
    get_stats,
    list_companies,
    list_company_sources,
    list_ingestion_history,
)
from outreach.ingestion import (
    CompanySource,
    DomainTextSource,
    JSONCompanySource,
    ProfileTargetFilter,
    RawCompanyRecord,
    SECCompanySource,
    ingest_source,
)
from outreach.models import Profile


class MemorySource(CompanySource):
    def __init__(self, name, records):
        self.name = name
        self.identifier = f"memory:{name}"
        self._records = records

    def records(self):
        yield from self._records


def technical_profile():
    return Profile(
        name="Ty",
        education="BSc",
        field_of_study="Computer Science",
        target_roles=["Cloud Engineer", "Software Engineer"],
        skills=["Python", "AWS"],
        work_styles=["remote"],
        location="Texas",
        tone="direct",
    )


def test_source_pipeline_tracks_summary_metadata_and_history(tmp_path):
    database = tmp_path / "outreach.db"
    source = MemorySource(
        "first-source",
        [RawCompanyRecord({"name": "Cloudflare", "domain": "cloudflare.com"}, "one")],
    )
    summary = ingest_source(source, database_path=database)
    company = list_companies(database_path=database)[0]
    sources = list_company_sources(company.id, database)
    history = list_ingestion_history(database)

    assert summary.processed == 1
    assert summary.imported == 1
    assert summary.duplicates == summary.filtered == summary.invalid == 0
    assert sources[0]["source_name"] == "first-source"
    assert sources[0]["source_identifier"] == "one"
    assert sources[0]["discovered_at"] is not None
    assert history[0].source_name == "first-source"
    assert history[0].completed_at is not None
    assert history[0].processed == history[0].imported == 1


def test_duplicate_across_sources_keeps_one_company_and_both_sources(tmp_path):
    database = tmp_path / "outreach.db"
    ingest_source(
        MemorySource(
            "source-a",
            [RawCompanyRecord({"name": "Cloudflare", "website": "https://cloudflare.com"})],
        ),
        database_path=database,
    )
    summary = ingest_source(
        MemorySource(
            "source-b",
            [RawCompanyRecord({"name": "Cloudflare Inc", "domain": "www.cloudflare.com"})],
        ),
        database_path=database,
    )
    companies = list_companies(database_path=database)
    assert len(companies) == 1
    assert summary.duplicates == 1
    assert {item["source_name"] for item in list_company_sources(companies[0].id, database)} == {
        "source-a",
        "source-b",
    }


def test_domain_text_import_derives_names_and_counts_invalid(tmp_path):
    database = tmp_path / "outreach.db"
    path = tmp_path / "domains.txt"
    path.write_text(
        "cloudflare.com\nhttps://www.fastly.com/jobs\ncareers.example.com\nnot a domain\n",
        encoding="utf-8",
    )
    summary = ingest_source(DomainTextSource(path), database_path=database)
    companies = list_companies(database_path=database)
    assert summary.processed == 4
    assert summary.imported == 3
    assert summary.invalid == 1
    assert [(item.name, item.domain) for item in companies] == [
        ("Cloudflare", "cloudflare.com"),
        ("Fastly", "fastly.com"),
        ("Example", "example.com"),
    ]


def test_json_import_supports_optional_fields_and_malformed_records(tmp_path):
    database = tmp_path / "outreach.db"
    path = tmp_path / "companies.json"
    path.write_text(
        json.dumps(
            [
                {
                    "name": "Render",
                    "website": "https://render.com/",
                    "careers_url": "https://render.com/careers",
                },
                {"domain": "datadoghq.com"},
                "invalid",
            ]
        ),
        encoding="utf-8",
    )
    summary = ingest_source(JSONCompanySource(path), database_path=database)
    companies = list_companies(database_path=database)
    assert summary.imported == 2
    assert summary.invalid == 1
    assert [(item.name, item.domain) for item in companies] == [
        ("Render", "render.com"),
        ("Datadoghq", "datadoghq.com"),
    ]


def test_optional_profile_filter_is_conservative(tmp_path):
    database = tmp_path / "outreach.db"
    source = MemorySource(
        "filtered-source",
        [
            RawCompanyRecord({"name": "Tech Co", "industry": "software"}),
            RawCompanyRecord({"name": "Example Investment Trust", "industry": "investment trust"}),
            RawCompanyRecord({"name": "Unknown Co"}),
        ],
    )
    summary = ingest_source(
        source,
        database_path=database,
        company_filter=ProfileTargetFilter(technical_profile()),
    )
    assert summary.imported == 2
    assert summary.filtered == 1
    assert [item.name for item in list_companies(database_path=database)] == [
        "Tech Co",
        "Unknown Co",
    ]


def test_batch_import_processes_records_incrementally(tmp_path):
    database = tmp_path / "outreach.db"
    path = tmp_path / "domains.txt"
    path.write_text(
        "".join(f"company{index}.example\n" for index in range(250)),
        encoding="utf-8",
    )
    summary = ingest_source(DomainTextSource(path), database_path=database)
    assert summary.processed == summary.imported == 250
    assert get_stats(database)["total"] == 250


def test_sec_public_dataset_adapter(tmp_path):
    database = tmp_path / "outreach.db"
    path = tmp_path / "company_tickers.json"
    path.write_text(
        json.dumps(
            {
                "0": {"cik_str": 1652044, "ticker": "GOOG", "title": "Alphabet Inc."},
                "1": {"cik_str": 1018724, "ticker": "AMZN", "title": "Amazon.com Inc."},
            }
        ),
        encoding="utf-8",
    )
    summary = ingest_source(SECCompanySource(path), database_path=database)
    assert summary.imported == 2
    companies = list_companies(database_path=database)
    assert [item.name for item in companies] == ["Alphabet Inc.", "Amazon.com Inc."]
    assert list_company_sources(companies[0].id, database)[0][
        "source_identifier"
    ] == "sec-cik:1652044"
