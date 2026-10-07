# OutReach

OutReach is a local Python CLI for building a broad employer database and
managing a job-outreach queue. It does not send email or scrape websites.

## Company ingestion

All commands should be run from the project root after installing the package:

```bash
python -m pip install .
python -m outreach ingest csv companies.csv
python -m outreach ingest domains domains.txt
python -m outreach ingest json companies.json
python -m outreach ingest history
```

Use `--filter-profile` with an import command to apply the conservative,
rule-based candidate-profile filter. Records with missing classification data
are retained.

CSV supports these columns; only `name` is required:

```text
name,website,domain,careers_url,contact_email,contact_type,industry,category,description
```

Domain files contain one domain or URL per line. Blank lines and lines starting
with `#` are ignored.

JSON files contain a list of objects, or an object with a `companies` list:

```json
[
  {"name": "Cloudflare", "website": "https://cloudflare.com"},
  {"name": "Fastly", "domain": "fastly.com"}
]
```

## SEC public company dataset

The SEC publishes `company_tickers.json`, containing EDGAR company names,
tickers, and CIK identifiers. Download it manually from:

```text
https://www.sec.gov/files/company_tickers.json
```

Then import it with:

```bash
python -m outreach ingest sec company_tickers.json
```

The adapter uses `title` as the company name and `cik_str` as source metadata.
The file does not contain company domains, so duplicate fallback uses normalized
company names. The source is only an initial public-company universe; SEC notes
that its ticker associations do not guarantee complete accuracy or scope.

## Public contact discovery

Contact discovery performs a shallow, robots-aware crawl of public company
pages. It checks at most four HTML pages by default, applies a delay between
requests, and never submits forms, guesses email addresses, probes mailboxes,
or accesses login-protected content.

```bash
python -m outreach contacts discover 1
python -m outreach contacts discover-all --limit 50
python -m outreach contacts list 1
python -m outreach contacts best 1
python -m outreach contacts stats
```

The normalized `contact_routes` table is the source of truth for discovered
routes. The legacy company `contact_email` and `contact_type` fields remain for
compatibility and are populated only when a directly observed public email is
the company's best active route.
