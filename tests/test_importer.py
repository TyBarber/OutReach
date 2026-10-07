from outreach.database import import_companies_csv, list_companies


def test_csv_import_continues_past_duplicates_and_invalid_rows(tmp_path):
    database = tmp_path / "outreach.db"
    csv_path = tmp_path / "companies.csv"
    csv_path.write_text(
        "name,website,domain,careers_url,contact_email,contact_type\n"
        "Cloudflare,https://www.cloudflare.com,,,,careers\n"
        "Cloudflare Inc,,cloudflare.com,,,general\n"
        ",,missing-name.test,,,general\n"
        "Example,,example.com,,hello@example.com,general\n",
        encoding="utf-8",
    )
    summary = import_companies_csv(csv_path, database)
    assert summary.imported == 2
    assert summary.duplicates == 1
    assert summary.invalid == 1
    assert [company.domain for company in list_companies(database_path=database)] == [
        "cloudflare.com",
        "example.com",
    ]
