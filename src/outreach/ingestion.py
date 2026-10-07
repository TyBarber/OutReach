import csv
import json
import re
from abc import ABC, abstractmethod
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from outreach.database import (
    DATABASE_PATH,
    DuplicateCompanyError,
    add_company,
    add_company_source,
    complete_ingestion,
    find_company_id,
    normalize_domain,
    start_ingestion,
)
from outreach.models import CONTACT_TYPES, Company, Profile


@dataclass(frozen=True)
class RawCompanyRecord:
    data: Any
    source_identifier: str | None = None


@dataclass(frozen=True)
class IngestionSummary:
    source: str
    processed: int = 0
    imported: int = 0
    duplicates: int = 0
    filtered: int = 0
    invalid: int = 0


class CompanySource(ABC):
    name: str
    identifier: str | None

    @abstractmethod
    def records(self) -> Iterator[RawCompanyRecord]:
        """Yield raw records incrementally where the file format permits."""


class CompanyFilter(Protocol):
    def include(self, company: Company, metadata: Mapping[str, Any]) -> bool: ...


class CSVCompanySource(CompanySource):
    name = "csv"

    def __init__(self, path: Path, *, name: str = "csv") -> None:
        self.path = path
        self.name = name
        self.identifier = str(path)

    def records(self) -> Iterator[RawCompanyRecord]:
        with self.path.open(newline="", encoding="utf-8-sig") as file:
            reader = csv.DictReader(file)
            if not reader.fieldnames or "name" not in reader.fieldnames:
                raise ValueError("CSV must contain a 'name' column.")
            for line_number, row in enumerate(reader, start=2):
                data = row if (row.get("name") or "").strip() else None
                yield RawCompanyRecord(data, f"{self.path}:{line_number}")


class DomainTextSource(CompanySource):
    name = "domains"

    def __init__(self, path: Path) -> None:
        self.path = path
        self.identifier = str(path)

    def records(self) -> Iterator[RawCompanyRecord]:
        with self.path.open(encoding="utf-8-sig") as file:
            for line_number, line in enumerate(file, start=1):
                value = line.strip()
                if not value or value.startswith("#"):
                    continue
                domain = primary_domain(value)
                yield RawCompanyRecord(
                    {"name": derive_company_name(domain), "domain": domain},
                    f"{self.path}:{line_number}",
                )


class JSONCompanySource(CompanySource):
    name = "json"

    def __init__(self, path: Path) -> None:
        self.path = path
        self.identifier = str(path)

    def records(self) -> Iterator[RawCompanyRecord]:
        with self.path.open(encoding="utf-8-sig") as file:
            payload = json.load(file)
        if isinstance(payload, dict):
            payload = payload.get("companies")
        if not isinstance(payload, list):
            raise ValueError("JSON must be a list or an object with a 'companies' list.")
        for index, item in enumerate(payload):
            yield RawCompanyRecord(item, f"{self.path}#{index}")


class SECCompanySource(CompanySource):
    """Adapter for the SEC company_tickers.json public dataset."""

    name = "sec-company-tickers"

    def __init__(self, path: Path) -> None:
        self.path = path
        self.identifier = str(path)

    def records(self) -> Iterator[RawCompanyRecord]:
        with self.path.open(encoding="utf-8-sig") as file:
            payload = json.load(file)
        if not isinstance(payload, dict):
            raise ValueError("SEC dataset must be a JSON object.")
        for item in payload.values():
            if not isinstance(item, dict):
                yield RawCompanyRecord(item)
                continue
            cik = item.get("cik_str")
            yield RawCompanyRecord(
                {
                    "name": item.get("title"),
                    "industry": "public company",
                },
                f"sec-cik:{cik}" if cik is not None else None,
            )


class ProfileTargetFilter:
    """Conservative rule filter; missing information always passes."""

    _EXCLUDED = {
        "individual",
        "municipality",
        "school district",
        "church",
        "homeowners association",
        "investment fund",
        "mutual fund",
        "etf",
        "trust",
    }
    _TECHNICAL = {
        "cloud",
        "software",
        "technology",
        "engineering",
        "infrastructure",
        "systems",
        "developer",
        "data",
        "security",
        "network",
        "internet",
    }

    def __init__(self, profile: Profile) -> None:
        profile_text = " ".join(
            [profile.field_of_study, *profile.target_roles, *profile.skills]
        ).casefold()
        self.keywords = {
            token
            for token in re.findall(r"[a-z0-9+#.]+", profile_text)
            if len(token) >= 3
        } | self._TECHNICAL

    def include(self, company: Company, metadata: Mapping[str, Any]) -> bool:
        details = " ".join(
            str(metadata.get(key) or "")
            for key in ("industry", "category", "description")
        ).strip().casefold()
        searchable = f"{company.name.casefold()} {details}"
        if not details:
            return True
        if any(keyword in searchable for keyword in self.keywords):
            return True
        return not any(term in searchable for term in self._EXCLUDED)


_COMMON_SUBDOMAINS = {"careers", "jobs", "hiring", "work", "apply"}
_SECOND_LEVEL_SUFFIXES = {"co.uk", "com.au", "co.nz", "co.jp", "com.br"}


def primary_domain(value: str | None) -> str | None:
    domain = normalize_domain(value)
    if not domain:
        return None
    labels = domain.split(".")
    if len(labels) > 2 and labels[0] in _COMMON_SUBDOMAINS:
        return ".".join(labels[1:])
    return domain


def derive_company_name(domain: str | None) -> str:
    if not domain:
        return ""
    labels = domain.split(".")
    suffix = ".".join(labels[-2:])
    label = labels[-3] if suffix in _SECOND_LEVEL_SUFFIXES and len(labels) >= 3 else labels[-2]
    return label.replace("-", " ").replace("_", " ").title()


def _clean(value: Any) -> str | None:
    if value is None:
        return None
    cleaned = str(value).strip()
    return cleaned or None


def company_from_record(record: RawCompanyRecord) -> tuple[Company, Mapping[str, Any]]:
    if not isinstance(record.data, Mapping):
        raise ValueError("Company record must be an object.")
    data = record.data
    website = _clean(data.get("website"))
    domain = primary_domain(_clean(data.get("domain")) or website)
    name = _clean(data.get("name")) or derive_company_name(domain)
    if not name:
        raise ValueError("Company name or valid domain is required.")
    contact_type = _clean(data.get("contact_type"))
    if contact_type and contact_type not in CONTACT_TYPES:
        raise ValueError(f"Unknown contact type: {contact_type}")
    return (
        Company(
            name=name,
            domain=domain,
            website=website,
            careers_url=_clean(data.get("careers_url")),
            contact_email=_clean(data.get("contact_email")),
            contact_type=contact_type,
        ),
        data,
    )


def ingest_source(
    source: CompanySource,
    *,
    database_path: Path = DATABASE_PATH,
    company_filter: CompanyFilter | None = None,
) -> IngestionSummary:
    ingestion_id = start_ingestion(source.name, source.identifier, database_path)
    processed = imported = duplicates = filtered = invalid = 0
    try:
        for record in source.records():
            processed += 1
            try:
                company, metadata = company_from_record(record)
                if company_filter and not company_filter.include(company, metadata):
                    filtered += 1
                    continue
                try:
                    company_id = add_company(company, database_path)
                    imported += 1
                except DuplicateCompanyError:
                    company_id = find_company_id(company, database_path)
                    duplicates += 1
                if company_id is not None:
                    add_company_source(
                        company_id,
                        source.name,
                        record.source_identifier or source.identifier,
                        database_path,
                    )
            except (TypeError, ValueError):
                invalid += 1
    finally:
        complete_ingestion(
            ingestion_id,
            processed=processed,
            imported=imported,
            duplicates=duplicates,
            filtered=filtered,
            invalid=invalid,
            database_path=database_path,
        )
    return IngestionSummary(
        source.name, processed, imported, duplicates, filtered, invalid
    )
