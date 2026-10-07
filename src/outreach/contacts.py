import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.utils import parseaddr
from html.parser import HTMLParser
from pathlib import Path
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import unquote, urljoin, urlsplit, urlunsplit
from urllib.request import Request, urlopen
from urllib.robotparser import RobotFileParser

from outreach.database import (
    DATABASE_PATH,
    _format_timestamp,
    _parse_timestamp,
    get_company,
    get_connection,
    initialize_database,
    normalize_domain,
    update_company,
)
from outreach.models import (
    CONTACT_ROUTE_STATUSES,
    CONTACT_ROUTE_TYPES,
    ContactDiscoveryState,
    ContactRoute,
)


USER_AGENT = "OutReach-PublicContactDiscovery/0.1 (+https://github.com/TyBarber/OutReach)"
EMAIL_PATTERN = re.compile(
    r"(?<![\w.+-])([A-Z0-9.!#$%&'*+/=?^_`{|}~-]+@"
    r"[A-Z0-9](?:[A-Z0-9-]{0,61}[A-Z0-9])?"
    r"(?:\.[A-Z0-9](?:[A-Z0-9-]{0,61}[A-Z0-9])?)+)",
    re.IGNORECASE,
)
RELEVANT_TERMS = (
    "career",
    "jobs",
    "join us",
    "work with us",
    "hiring",
    "recruit",
    "talent",
    "people",
    "contact",
)
ROUTE_PRIORITY = {
    "recruiting_email": 1,
    "careers_email": 2,
    "talent_email": 3,
    "hiring_email": 4,
    "hr_email": 5,
    "careers_form": 6,
    "contact_form": 7,
    "general_email": 8,
    "careers_page": 9,
    "contact_page": 10,
    "other": 11,
}
LEGACY_CONTACT_TYPES = {
    "recruiting_email": "recruiting",
    "careers_email": "careers",
    "talent_email": "talent",
    "hiring_email": "hiring",
    "hr_email": "hr",
    "general_email": "general",
}


class ContactDiscoveryError(RuntimeError):
    pass


@dataclass(frozen=True)
class FetchResponse:
    url: str
    status: int
    text: str
    content_type: str = "text/html"


class Fetcher(Protocol):
    def fetch(self, url: str) -> FetchResponse: ...


class UrllibFetcher:
    def __init__(self, timeout: float = 10.0, max_bytes: int = 2_000_000) -> None:
        self.timeout = timeout
        self.max_bytes = max_bytes

    def fetch(self, url: str) -> FetchResponse:
        request = Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with urlopen(request, timeout=self.timeout) as response:
                content_type = response.headers.get_content_type()
                charset = response.headers.get_content_charset() or "utf-8"
                body = response.read(self.max_bytes + 1)
                if len(body) > self.max_bytes:
                    raise ContactDiscoveryError("Response exceeded the size limit.")
                return FetchResponse(
                    response.geturl(),
                    response.status,
                    body.decode(charset, errors="replace"),
                    content_type,
                )
        except HTTPError as error:
            return FetchResponse(url, error.code, "", "text/plain")
        except (TimeoutError, URLError, OSError) as error:
            raise ContactDiscoveryError(str(error)) from error


class _RateLimitedFetcher:
    def __init__(self, fetcher: Fetcher, delay: float) -> None:
        self.fetcher = fetcher
        self.delay = max(0.0, delay)
        self._last_request_at: float | None = None

    def fetch(self, url: str) -> FetchResponse:
        if self._last_request_at is not None and self.delay:
            remaining = self.delay - (time.monotonic() - self._last_request_at)
            if remaining > 0:
                time.sleep(remaining)
        try:
            return self.fetcher.fetch(url)
        finally:
            self._last_request_at = time.monotonic()


class _PageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[tuple[str, str]] = []
        self.forms: list[str | None] = []
        self.text_parts: list[str] = []
        self._active_href: str | None = None
        self._anchor_text: list[str] = []

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == "a":
            self._active_href = attributes.get("href")
            self._anchor_text = []
        elif tag == "form":
            self.forms.append(attributes.get("action"))

    def handle_endtag(self, tag):
        if tag == "a" and self._active_href is not None:
            self.links.append((self._active_href, " ".join(self._anchor_text)))
            self._active_href = None
            self._anchor_text = []

    def handle_data(self, data):
        text = data.strip()
        if text:
            self.text_parts.append(text)
            if self._active_href is not None:
                self._anchor_text.append(text)

    @property
    def text(self) -> str:
        return " ".join(self.text_parts)


@dataclass(frozen=True)
class DiscoveryResult:
    company_id: int
    company_name: str
    status: str
    pages_checked: int
    routes: tuple[ContactRoute, ...]
    error_message: str | None = None


@dataclass(frozen=True)
class BatchDiscoverySummary:
    processed: int = 0
    successes: int = 0
    no_routes: int = 0
    failures: int = 0
    routes_discovered: int = 0


def normalize_email(value: str) -> str | None:
    value = unquote(value).strip().strip("<>[](){}.,;:'\"")
    _, address = parseaddr(value)
    if not address or not EMAIL_PATTERN.fullmatch(address):
        return None
    local, domain = address.rsplit("@", 1)
    domain = domain.rstrip(".").lower()
    if not normalize_domain(domain):
        return None
    return f"{local}@{domain}"


def classify_email(email: str, context: str = "") -> str:
    local = email.split("@", 1)[0].casefold()
    combined = f"{local} {context.casefold()}"
    if local in {"hello", "info", "contact", "support", "office", "admin"}:
        return "general_email"
    if "recruit" in combined:
        return "recruiting_email"
    if any(term in combined for term in ("career", "jobs", "job")):
        return "careers_email"
    if "talent" in combined:
        return "talent_email"
    if "hiring" in combined:
        return "hiring_email"
    if re.search(r"(^|[._-])hr($|[._-])", local) or "human resources" in combined:
        return "hr_email"
    return "general_email"


def _normalize_url(value: str) -> str | None:
    try:
        parsed = urlsplit(value)
    except ValueError:
        return None
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return None
    return urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), parsed.path or "/", parsed.query, ""))


def add_contact_route(
    route: ContactRoute, database_path: Path = DATABASE_PATH
) -> int:
    initialize_database(database_path)
    if route.route_type not in CONTACT_ROUTE_TYPES:
        raise ValueError(f"Unknown route type: {route.route_type}")
    if route.status not in CONTACT_ROUTE_STATUSES:
        raise ValueError(f"Unknown route status: {route.status}")
    if route.route_type.endswith("_email"):
        value = normalize_email(route.value)
    else:
        value = _normalize_url(route.value)
    if value is None:
        raise ValueError(f"Invalid contact route value: {route.value}")
    confidence = min(1.0, max(0.0, float(route.confidence)))
    connection = get_connection(database_path)
    try:
        connection.execute(
            """
            INSERT INTO contact_routes (
                company_id, route_type, value, source_url, source_type,
                confidence, verified, discovered_at, last_checked_at,
                status, notes
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(company_id, value) DO UPDATE SET
                route_type = excluded.route_type,
                source_url = excluded.source_url,
                source_type = excluded.source_type,
                confidence = max(contact_routes.confidence, excluded.confidence),
                verified = max(contact_routes.verified, excluded.verified),
                last_checked_at = excluded.last_checked_at,
                status = excluded.status,
                notes = coalesce(excluded.notes, contact_routes.notes)
            """,
            (
                route.company_id,
                route.route_type,
                value,
                route.source_url,
                route.source_type,
                confidence,
                int(route.verified),
                _format_timestamp(route.discovered_at),
                _format_timestamp(route.last_checked_at),
                route.status,
                route.notes,
            ),
        )
        row = connection.execute(
            """
            SELECT id FROM contact_routes
            WHERE company_id = ? AND value = ?
            """,
            (route.company_id, value),
        ).fetchone()
        connection.commit()
        return int(row["id"])
    finally:
        connection.close()


def _row_to_route(row) -> ContactRoute:
    return ContactRoute(
        id=row["id"],
        company_id=row["company_id"],
        route_type=row["route_type"],
        value=row["value"],
        source_url=row["source_url"],
        source_type=row["source_type"],
        confidence=row["confidence"],
        verified=bool(row["verified"]),
        discovered_at=_parse_timestamp(row["discovered_at"]),
        last_checked_at=_parse_timestamp(row["last_checked_at"]),
        status=row["status"],
        notes=row["notes"],
    )


def list_contact_routes(
    company_id: int,
    *,
    active_only: bool = False,
    database_path: Path = DATABASE_PATH,
) -> list[ContactRoute]:
    initialize_database(database_path)
    connection = get_connection(database_path)
    try:
        if active_only:
            rows = connection.execute(
                "SELECT * FROM contact_routes WHERE company_id = ? AND status = 'active'",
                (company_id,),
            ).fetchall()
        else:
            rows = connection.execute(
                "SELECT * FROM contact_routes WHERE company_id = ?", (company_id,)
            ).fetchall()
        routes = [_row_to_route(row) for row in rows]
        return sorted(routes, key=lambda route: (ROUTE_PRIORITY[route.route_type], route.id or 0))
    finally:
        connection.close()


def get_best_contact_route(
    company_id: int, database_path: Path = DATABASE_PATH
) -> ContactRoute | None:
    routes = list_contact_routes(company_id, active_only=True, database_path=database_path)
    return routes[0] if routes else None


def set_discovery_state(
    state: ContactDiscoveryState, database_path: Path = DATABASE_PATH
) -> None:
    initialize_database(database_path)
    connection = get_connection(database_path)
    try:
        connection.execute(
            """
            INSERT INTO contact_discovery_state (
                company_id, last_discovery_at, discovery_status,
                pages_checked, routes_found, error_message
            ) VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(company_id) DO UPDATE SET
                last_discovery_at = excluded.last_discovery_at,
                discovery_status = excluded.discovery_status,
                pages_checked = excluded.pages_checked,
                routes_found = excluded.routes_found,
                error_message = excluded.error_message
            """,
            (
                state.company_id,
                _format_timestamp(state.last_discovery_at),
                state.discovery_status,
                state.pages_checked,
                state.routes_found,
                state.error_message,
            ),
        )
        connection.commit()
    finally:
        connection.close()


def get_discovery_state(
    company_id: int, database_path: Path = DATABASE_PATH
) -> ContactDiscoveryState | None:
    initialize_database(database_path)
    connection = get_connection(database_path)
    try:
        row = connection.execute(
            "SELECT * FROM contact_discovery_state WHERE company_id = ?",
            (company_id,),
        ).fetchone()
        if not row:
            return None
        return ContactDiscoveryState(
            company_id=row["company_id"],
            last_discovery_at=_parse_timestamp(row["last_discovery_at"]),
            discovery_status=row["discovery_status"],
            pages_checked=row["pages_checked"],
            routes_found=row["routes_found"],
            error_message=row["error_message"],
        )
    finally:
        connection.close()


def _page_route_type(url: str, text: str = "") -> str | None:
    context = f"{urlsplit(url).path} {text}".casefold()
    if any(term in context for term in ("career", "jobs", "join us", "work with us", "hiring", "talent", "recruit")):
        return "careers_page"
    if "contact" in context:
        return "contact_page"
    return None


def _base_url(website: str | None, domain: str | None) -> str | None:
    value = website or (f"https://{domain}" if domain else None)
    if not value:
        return None
    if "://" not in value:
        value = f"https://{value}"
    normalized = _normalize_url(value)
    if not normalized:
        return None
    parsed = urlsplit(normalized)
    return urlunsplit((parsed.scheme, parsed.netloc, "/", "", ""))


def _same_company_site(url: str, company_domain: str | None) -> bool:
    hostname = normalize_domain(url)
    return bool(hostname and company_domain and (hostname == company_domain or hostname.endswith(f".{company_domain}")))


def _robots_allowed(url: str, fetcher: Fetcher, cache: dict[str, RobotFileParser | None]) -> bool:
    parsed = urlsplit(url)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    if origin not in cache:
        robots_url = f"{origin}/robots.txt"
        try:
            response = fetcher.fetch(robots_url)
            if 200 <= response.status < 300 and response.text:
                parser = RobotFileParser()
                parser.set_url(robots_url)
                parser.parse(response.text.splitlines())
                cache[origin] = parser
            else:
                cache[origin] = None
        except ContactDiscoveryError:
            cache[origin] = None
    parser = cache[origin]
    return True if parser is None else parser.can_fetch(USER_AGENT, url)


def discover_company_contacts(
    company_id: int,
    *,
    database_path: Path = DATABASE_PATH,
    fetcher: Fetcher | None = None,
    page_budget: int = 4,
    request_delay: float = 0.25,
) -> DiscoveryResult:
    company = get_company(company_id, database_path)
    if company is None:
        raise ValueError(f"Company {company_id} not found.")
    now = datetime.now(timezone.utc)
    set_discovery_state(
        ContactDiscoveryState(company_id, now, "pending"), database_path
    )
    base_url = _base_url(company.website, company.domain)
    if not base_url:
        state = ContactDiscoveryState(company_id, now, "failed", error_message="No website or domain.")
        set_discovery_state(state, database_path)
        return DiscoveryResult(company_id, company.name, "failed", 0, (), state.error_message)

    fetcher = _RateLimitedFetcher(fetcher or UrllibFetcher(), request_delay)
    company_domain = normalize_domain(company.domain or base_url)
    queue = [base_url]
    if company.careers_url:
        careers_url = _normalize_url(company.careers_url)
        if careers_url and careers_url not in queue:
            queue.append(careers_url)
    visited: set[str] = set()
    found_ids: set[int] = set()
    robots_cache: dict[str, RobotFileParser | None] = {}
    errors: list[str] = []
    pages_checked = 0

    while queue and pages_checked < max(1, page_budget):
        url = queue.pop(0)
        if url in visited:
            continue
        visited.add(url)
        if not _robots_allowed(url, fetcher, robots_cache):
            errors.append(f"robots.txt disallows {url}")
            continue
        try:
            response = fetcher.fetch(url)
        except ContactDiscoveryError as error:
            errors.append(f"{url}: {error}")
            continue
        if not 200 <= response.status < 300:
            errors.append(f"{url}: HTTP {response.status}")
            continue
        if "html" not in response.content_type:
            continue
        pages_checked += 1
        page_url = _normalize_url(response.url) or url
        parser = _PageParser()
        try:
            parser.feed(response.text)
        except Exception as error:
            errors.append(f"{page_url}: malformed HTML ({error})")
            continue
        page_type = _page_route_type(page_url)
        if page_type:
            found_ids.add(
                add_contact_route(
                    ContactRoute(
                        company_id,
                        page_type,
                        page_url,
                        source_url=page_url,
                        confidence=0.8,
                        verified=True,
                    ),
                    database_path,
                )
            )

        emails = set(EMAIL_PATTERN.findall(parser.text))
        for href, _ in parser.links:
            if href.casefold().startswith("mailto:"):
                emails.add(href.split(":", 1)[1].split("?", 1)[0])
        for raw_email in emails:
            email = normalize_email(raw_email)
            if not email:
                continue
            route_type = classify_email(email, f"{page_url} {parser.text[:500]}")
            email_domain = normalize_domain(email.rsplit("@", 1)[1])
            confidence = 0.95 if email_domain == company_domain else 0.75
            found_ids.add(
                add_contact_route(
                    ContactRoute(
                        company_id,
                        route_type,
                        email,
                        source_url=page_url,
                        confidence=confidence,
                        verified=True,
                    ),
                    database_path,
                )
            )

        for href, text in parser.links:
            if href.casefold().startswith(("mailto:", "tel:", "javascript:")):
                continue
            linked_url = _normalize_url(urljoin(page_url, href))
            if not linked_url:
                continue
            route_type = _page_route_type(linked_url, text)
            if not route_type:
                continue
            found_ids.add(
                add_contact_route(
                    ContactRoute(
                        company_id,
                        route_type,
                        linked_url,
                        source_url=page_url,
                        confidence=0.7,
                        verified=True,
                    ),
                    database_path,
                )
            )
            if _same_company_site(linked_url, company_domain) and linked_url not in visited:
                queue.append(linked_url)

        if parser.forms and page_type:
            form_type = "careers_form" if page_type == "careers_page" else "contact_form"
            for action in parser.forms:
                form_url = _normalize_url(urljoin(page_url, action)) if action else page_url
                if form_url:
                    found_ids.add(
                        add_contact_route(
                            ContactRoute(
                                company_id,
                                form_type,
                                form_url,
                                source_url=page_url,
                                confidence=0.9,
                                verified=True,
                            ),
                            database_path,
                        )
                    )

    routes = tuple(
        route
        for route in list_contact_routes(company_id, database_path=database_path)
        if route.id in found_ids
    )
    if routes:
        status = "success"
    elif pages_checked:
        status = "no_routes"
    else:
        status = "failed"
    error_message = "; ".join(errors)[:1000] or None
    set_discovery_state(
        ContactDiscoveryState(
            company_id,
            datetime.now(timezone.utc),
            status,
            pages_checked,
            len(routes),
            error_message,
        ),
        database_path,
    )
    best = get_best_contact_route(company_id, database_path)
    if best and best.route_type.endswith("_email") and not company.contact_email:
        update_company(
            company_id,
            contact_email=best.value,
            contact_type=LEGACY_CONTACT_TYPES[best.route_type],
            database_path=database_path,
        )
    return DiscoveryResult(
        company_id, company.name, status, pages_checked, routes, error_message
    )


def _discovery_candidates(
    *,
    limit: int,
    retry_failed: bool,
    database_path: Path,
    stale_days: int = 7,
) -> list[int]:
    initialize_database(database_path)
    cutoff = _format_timestamp(datetime.now(timezone.utc) - timedelta(days=stale_days))
    connection = get_connection(database_path)
    try:
        rows = connection.execute(
            """
            SELECT companies.id
            FROM companies
            LEFT JOIN contact_discovery_state AS state
                ON state.company_id = companies.id
            WHERE companies.status <> 'do_not_contact'
              AND (companies.website IS NOT NULL OR companies.domain IS NOT NULL)
              AND (
                    state.company_id IS NULL
                    OR state.last_discovery_at < ?
                    OR (? = 1 AND state.discovery_status = 'failed')
              )
            ORDER BY companies.id
            LIMIT ?
            """,
            (cutoff, int(retry_failed), limit),
        ).fetchall()
        return [int(row["id"]) for row in rows]
    finally:
        connection.close()


def discover_contacts_batch(
    *,
    limit: int = 50,
    retry_failed: bool = False,
    database_path: Path = DATABASE_PATH,
    fetcher: Fetcher | None = None,
    page_budget: int = 4,
    request_delay: float = 0.25,
    company_delay: float = 0.5,
) -> BatchDiscoverySummary:
    if limit < 1:
        raise ValueError("Limit must be at least 1.")
    processed = successes = no_routes = failures = routes_discovered = 0
    for company_id in _discovery_candidates(
        limit=limit,
        retry_failed=retry_failed,
        database_path=database_path,
    ):
        if processed and company_delay > 0:
            time.sleep(company_delay)
        processed += 1
        try:
            result = discover_company_contacts(
                company_id,
                database_path=database_path,
                fetcher=fetcher,
                page_budget=page_budget,
                request_delay=request_delay,
            )
            routes_discovered += len(result.routes)
            if result.status == "success":
                successes += 1
            elif result.status == "no_routes":
                no_routes += 1
            else:
                failures += 1
        except Exception as error:
            failures += 1
            set_discovery_state(
                ContactDiscoveryState(
                    company_id,
                    datetime.now(timezone.utc),
                    "failed",
                    error_message=str(error)[:1000],
                ),
                database_path,
            )
    return BatchDiscoverySummary(
        processed, successes, no_routes, failures, routes_discovered
    )


def get_contact_stats(database_path: Path = DATABASE_PATH) -> dict[str, int]:
    initialize_database(database_path)
    connection = get_connection(database_path)
    try:
        row = connection.execute(
            """
            SELECT
                count(*) AS total_routes,
                sum(status = 'active') AS active_routes,
                sum(verified = 1) AS verified_routes,
                count(DISTINCT company_id) AS companies_with_routes,
                sum(substr(route_type, -6) = '_email') AS email_routes,
                sum(substr(route_type, -5) = '_form') AS form_routes,
                sum(substr(route_type, -5) = '_page') AS page_routes
            FROM contact_routes
            """
        ).fetchone()
        attempts = connection.execute(
            "SELECT count(*) AS count FROM contact_discovery_state"
        ).fetchone()["count"]
        stats = {key: int(row[key] or 0) for key in row.keys()}
        stats["companies_checked"] = int(attempts)
        return stats
    finally:
        connection.close()
