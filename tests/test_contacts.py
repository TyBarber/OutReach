from datetime import datetime, timezone

import pytest

from outreach.contacts import (
    ContactDiscoveryError,
    FetchResponse,
    add_contact_route,
    classify_email,
    discover_company_contacts,
    discover_contacts_batch,
    get_best_contact_route,
    get_contact_stats,
    get_discovery_state,
    list_contact_routes,
    normalize_email,
)
from outreach.database import add_company, get_company, get_stats, set_company_status
from outreach.models import Company, ContactRoute


class MockFetcher:
    def __init__(self, responses):
        self.responses = responses
        self.requests = []

    def fetch(self, url):
        self.requests.append(url)
        response = self.responses.get(url)
        if isinstance(response, Exception):
            raise response
        if response is None:
            return FetchResponse(url, 404, "", "text/plain")
        return response


def response(url, html, status=200, content_type="text/html"):
    return FetchResponse(url, status, html, content_type)


def test_contact_route_insert_normalizes_and_prevents_duplicates(tmp_path):
    database = tmp_path / "outreach.db"
    company_id = add_company(Company("Example", "example.com"), database)
    first = add_contact_route(
        ContactRoute(
            company_id,
            "recruiting_email",
            " Recruiting@EXAMPLE.COM. ",
            source_url="https://example.com/careers",
            verified=True,
        ),
        database,
    )
    second = add_contact_route(
        ContactRoute(
            company_id,
            "recruiting_email",
            "recruiting@example.com",
            source_url="https://example.com/jobs",
            verified=True,
        ),
        database,
    )
    routes = list_contact_routes(company_id, database_path=database)
    assert first == second
    assert len(routes) == 1
    assert routes[0].value == "Recruiting@example.com"
    assert routes[0].verified is True
    assert get_stats(database)["with_contacts"] == 1


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (" jobs@EXAMPLE.COM. ", "jobs@example.com"),
        ("<hello@example.com>", "hello@example.com"),
        ("not-an-email", None),
        ("person@localhost", None),
    ],
)
def test_email_normalization(value, expected):
    assert normalize_email(value) == expected


@pytest.mark.parametrize(
    ("email", "expected"),
    [
        ("recruiting@example.com", "recruiting_email"),
        ("jobs@example.com", "careers_email"),
        ("talent@example.com", "talent_email"),
        ("hiring@example.com", "hiring_email"),
        ("hr@example.com", "hr_email"),
        ("hello@example.com", "general_email"),
    ],
)
def test_email_classification(email, expected):
    assert classify_email(email) == expected


def test_discovery_extracts_mailto_visible_email_pages_and_forms(tmp_path):
    database = tmp_path / "outreach.db"
    company_id = add_company(
        Company("Example", "example.com", website="https://example.com"),
        database,
    )
    fetcher = MockFetcher(
        {
            "https://example.com/robots.txt": response(
                "https://example.com/robots.txt", "User-agent: *\nAllow: /", content_type="text/plain"
            ),
            "https://example.com/": response(
                "https://example.com/",
                """
                <html><body>
                  <p>Email us at info@EXAMPLE.com.</p>
                  <a href="/careers">Join us</a>
                  <a href="/contact">Contact us</a>
                </body></html>
                """,
            ),
            "https://example.com/careers": response(
                "https://example.com/careers",
                """
                <html><body>
                  <a href="mailto:recruiting@example.com?subject=Hello">Apply</a>
                  <form action="/careers/apply"><input name="message"></form>
                </body></html>
                """,
            ),
            "https://example.com/contact": response(
                "https://example.com/contact",
                '<html><body><form action="/contact/send"></form></body></html>',
            ),
        }
    )
    result = discover_company_contacts(
        company_id,
        database_path=database,
        fetcher=fetcher,
        page_budget=3,
        request_delay=0,
    )
    route_types = {route.route_type for route in result.routes}
    assert result.status == "success"
    assert result.pages_checked == 3
    assert {
        "general_email",
        "recruiting_email",
        "careers_page",
        "contact_page",
        "careers_form",
        "contact_form",
    } <= route_types
    assert all(route.verified for route in result.routes)
    assert get_best_contact_route(company_id, database).value == "recruiting@example.com"
    compatible_company = get_company(company_id, database)
    assert compatible_company.contact_email == "recruiting@example.com"
    assert compatible_company.contact_type == "recruiting"
    state = get_discovery_state(company_id, database)
    assert state.discovery_status == "success"
    assert state.pages_checked == 3
    assert state.routes_found == len(result.routes)
    stats = get_contact_stats(database)
    assert stats["companies_with_routes"] == 1
    assert stats["companies_checked"] == 1


def test_best_route_uses_priority_and_ignores_inactive_routes(tmp_path):
    database = tmp_path / "outreach.db"
    company_id = add_company(Company("Example", "example.com"), database)
    add_contact_route(
        ContactRoute(company_id, "contact_page", "https://example.com/contact"),
        database,
    )
    add_contact_route(
        ContactRoute(company_id, "general_email", "hello@example.com"), database
    )
    add_contact_route(
        ContactRoute(
            company_id,
            "recruiting_email",
            "recruiting@example.com",
            status="stale",
        ),
        database,
    )
    assert get_best_contact_route(company_id, database).value == "hello@example.com"


def test_robots_denial_is_respected(tmp_path):
    database = tmp_path / "outreach.db"
    company_id = add_company(Company("Blocked", "blocked.example"), database)
    fetcher = MockFetcher(
        {
            "https://blocked.example/robots.txt": response(
                "https://blocked.example/robots.txt",
                "User-agent: *\nDisallow: /",
                content_type="text/plain",
            )
        }
    )
    result = discover_company_contacts(
        company_id, database_path=database, fetcher=fetcher, request_delay=0
    )
    assert result.status == "failed"
    assert result.pages_checked == 0
    assert fetcher.requests == ["https://blocked.example/robots.txt"]
    assert "robots.txt disallows" in result.error_message


def test_no_routes_records_discovery_state(tmp_path):
    database = tmp_path / "outreach.db"
    company_id = add_company(Company("Empty", "empty.example"), database)
    fetcher = MockFetcher(
        {
            "https://empty.example/": response(
                "https://empty.example/", "<html><body>Welcome</body></html>"
            )
        }
    )
    result = discover_company_contacts(
        company_id, database_path=database, fetcher=fetcher, request_delay=0
    )
    assert result.status == "no_routes"
    assert get_discovery_state(company_id, database).routes_found == 0


def test_batch_isolates_failures_and_skips_do_not_contact(tmp_path):
    database = tmp_path / "outreach.db"
    good_id = add_company(Company("Good", "good.example"), database)
    bad_id = add_company(Company("Bad", "bad.example"), database)
    blocked_id = add_company(Company("Blocked", "blocked.example"), database)
    set_company_status(blocked_id, "do_not_contact", database)
    fetcher = MockFetcher(
        {
            "https://good.example/": response(
                "https://good.example/",
                '<html><a href="mailto:jobs@good.example">Jobs</a></html>',
            ),
            "https://bad.example/": ContactDiscoveryError("timed out"),
        }
    )
    summary = discover_contacts_batch(
        limit=10,
        database_path=database,
        fetcher=fetcher,
        request_delay=0,
        company_delay=0,
    )
    assert summary.processed == 2
    assert summary.successes == 1
    assert summary.failures == 1
    assert summary.routes_discovered == 1
    assert get_discovery_state(good_id, database).discovery_status == "success"
    assert get_discovery_state(bad_id, database).discovery_status == "failed"
    assert get_discovery_state(blocked_id, database) is None


def test_route_model_timestamps_are_timezone_aware():
    route = ContactRoute(1, "general_email", "hello@example.com")
    assert route.discovered_at.tzinfo == timezone.utc
    assert route.last_checked_at.tzinfo == timezone.utc
    assert route.discovered_at <= datetime.now(timezone.utc)
