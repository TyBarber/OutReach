import argparse
import json
from pathlib import Path

from outreach.config import profile_path
from outreach.contacts import (
    discover_company_contacts,
    discover_contacts_batch,
    get_best_contact_route,
    get_contact_stats,
    list_contact_routes,
)
from outreach.database import (
    DuplicateCompanyError,
    add_company,
    get_company,
    get_outreach_queue,
    get_stats,
    import_companies_csv,
    list_companies,
    list_company_sources,
    list_ingestion_history,
    set_company_status,
)
from outreach.ingestion import (
    CSVCompanySource,
    DomainTextSource,
    JSONCompanySource,
    ProfileTargetFilter,
    SECCompanySource,
    ingest_source,
)
from outreach.models import CONTACT_TYPES, Company
from outreach.onboarding import run_onboarding
from outreach.store import load_profile, save_profile


def cmd_init() -> int:
    profile = run_onboarding()
    save_profile(profile, profile_path())
    print(f"\nSaved profile to {profile_path()}")
    return 0


def cmd_show_profile() -> int:
    try:
        profile = load_profile(profile_path())
    except FileNotFoundError as error:
        print(error)
        return 1
    print(json.dumps(profile.to_dict(), indent=2))
    return 0


def _optional_input(prompt: str) -> str | None:
    return input(prompt).strip() or None


def cmd_company_add() -> int:
    name = input("Company name: ").strip()
    website = _optional_input("Website (optional): ")
    domain = _optional_input("Domain (optional; derived from website if blank): ")
    careers_url = _optional_input("Careers URL (optional): ")
    contact_email = _optional_input("Contact email (optional): ")
    contact_type = _optional_input(
        f"Contact type ({'/'.join(CONTACT_TYPES)}, optional): "
    )
    try:
        company_id = add_company(
            Company(
                name=name,
                domain=domain,
                website=website,
                careers_url=careers_url,
                contact_email=contact_email,
                contact_type=contact_type,
            )
        )
    except (DuplicateCompanyError, ValueError) as error:
        print(f"Error: {error}")
        return 1
    print(f"Added company {company_id}: {name}")
    return 0


def _print_companies(companies: list[Company]) -> None:
    if not companies:
        print("No companies found.")
        return
    headers = ("ID", "Company", "Domain", "Contact", "Contact Type", "Status")
    rows = [
        (
            str(company.id),
            company.name,
            company.domain or "-",
            company.contact_email or "-",
            company.contact_type or "-",
            company.status,
        )
        for company in companies
    ]
    widths = [max(len(headers[i]), *(len(row[i]) for row in rows)) for i in range(6)]
    print("  ".join(value.ljust(widths[i]) for i, value in enumerate(headers)))
    print("  ".join("-" * width for width in widths))
    for row in rows:
        print("  ".join(value.ljust(widths[i]) for i, value in enumerate(row)))


def cmd_company_list(args: argparse.Namespace) -> int:
    _print_companies(
        list_companies(status=args.status, has_contact=args.has_contact)
    )
    return 0


def cmd_company_show(company_id: int) -> int:
    company = get_company(company_id)
    if company is None:
        print(f"Company {company_id} not found.")
        return 1
    values = company.to_dict()
    for key, value in values.items():
        print(f"{key}: {value}")
    sources = list_company_sources(company_id)
    if sources:
        print("sources:")
        for source in sources:
            identifier = source["source_identifier"] or "-"
            print(f"  {source['source_name']}: {identifier}")
    return 0


def cmd_company_status(company_id: int, status: str) -> int:
    if not set_company_status(company_id, status):
        print(f"Company {company_id} not found.")
        return 1
    print(f"Company {company_id} marked {status}.")
    return 0


def cmd_company_import(csv_path: str) -> int:
    try:
        summary = import_companies_csv(Path(csv_path))
    except (OSError, ValueError) as error:
        print(f"Error: {error}")
        return 1
    print(f"Imported: {summary.imported}")
    print(f"Duplicates skipped: {summary.duplicates}")
    print(f"Invalid rows: {summary.invalid}")
    return 0


def cmd_queue_list() -> int:
    _print_companies(get_outreach_queue())
    return 0


def cmd_stats() -> int:
    labels = {
        "total": "Total companies",
        "discovered": "Discovered",
        "with_contacts": "With contacts",
        "without_contacts": "Without contacts",
        "queued": "Queued",
        "sent": "Sent",
        "replied": "Replied",
        "failed": "Failed",
        "do_not_contact": "Do not contact",
        "source_records": "Source records",
        "ingestion_runs": "Ingestion runs",
    }
    stats = get_stats()
    for key, label in labels.items():
        print(f"{label}: {stats[key]}")
    return 0


def _profile_filter(enabled: bool) -> ProfileTargetFilter | None:
    if not enabled:
        return None
    return ProfileTargetFilter(load_profile(profile_path()))


def _print_ingestion_summary(summary) -> None:
    print(f"Source: {summary.source}")
    print(f"Processed: {summary.processed}")
    print(f"Imported: {summary.imported}")
    print(f"Duplicates: {summary.duplicates}")
    print(f"Filtered: {summary.filtered}")
    print(f"Invalid: {summary.invalid}")


def cmd_ingest(args: argparse.Namespace) -> int:
    if args.ingest_command == "history":
        history = list_ingestion_history()
        if not history:
            print("No ingestion history found.")
            return 0
        print("ID  Source               Processed  Imported  Duplicates  Filtered  Invalid")
        for item in history:
            print(
                f"{item.id:<3} {item.source_name:<20} {item.processed:<10} "
                f"{item.imported:<9} {item.duplicates:<11} "
                f"{item.filtered:<9} {item.invalid}"
            )
        return 0

    source_types = {
        "csv": CSVCompanySource,
        "domains": DomainTextSource,
        "json": JSONCompanySource,
        "sec": SECCompanySource,
    }
    try:
        source = source_types[args.ingest_command](Path(args.path))
        summary = ingest_source(
            source,
            company_filter=_profile_filter(args.filter_profile),
        )
    except (FileNotFoundError, OSError, ValueError) as error:
        print(f"Error: {error}")
        return 1
    _print_ingestion_summary(summary)
    return 0


def _print_routes(routes) -> None:
    if not routes:
        print("No contact routes found.")
        return
    for route in routes:
        verified = "verified" if route.verified else "unverified"
        print(f"{route.route_type}: {route.value} ({verified}, {route.status})")


def cmd_contacts(args: argparse.Namespace) -> int:
    if args.contacts_command == "discover":
        try:
            result = discover_company_contacts(
                args.id,
                page_budget=args.page_budget,
                request_delay=args.delay,
            )
        except ValueError as error:
            print(f"Error: {error}")
            return 1
        print(f"Company: {result.company_name}")
        print(f"Status: {result.status}")
        print(f"Pages checked: {result.pages_checked}")
        print("Found:")
        _print_routes(result.routes)
        best = get_best_contact_route(args.id)
        print(f"Best route: {best.value if best else 'None'}")
        if result.error_message:
            print(f"Notes: {result.error_message}")
        return 0 if result.status != "failed" else 1

    if args.contacts_command == "discover-all":
        summary = discover_contacts_batch(
            limit=args.limit,
            retry_failed=args.retry_failed,
            page_budget=args.page_budget,
            request_delay=args.delay,
            company_delay=args.company_delay,
        )
        print(f"Companies processed: {summary.processed}")
        print(f"Successes: {summary.successes}")
        print(f"No routes: {summary.no_routes}")
        print(f"Failures: {summary.failures}")
        print(f"Routes discovered: {summary.routes_discovered}")
        return 0

    if args.contacts_command == "list":
        if get_company(args.id) is None:
            print(f"Company {args.id} not found.")
            return 1
        _print_routes(list_contact_routes(args.id))
        return 0

    if args.contacts_command == "best":
        if get_company(args.id) is None:
            print(f"Company {args.id} not found.")
            return 1
        route = get_best_contact_route(args.id)
        if route is None:
            print("No active contact route found.")
            return 1
        print(f"{route.route_type}: {route.value}")
        return 0

    labels = {
        "total_routes": "Total routes",
        "active_routes": "Active routes",
        "verified_routes": "Verified routes",
        "companies_with_routes": "Companies with routes",
        "email_routes": "Email routes",
        "form_routes": "Form routes",
        "page_routes": "Page routes",
        "companies_checked": "Companies checked",
    }
    stats = get_contact_stats()
    for key, label in labels.items():
        print(f"{label}: {stats[key]}")
    return 0


def _add_company_commands(sub: argparse._SubParsersAction) -> None:
    company = sub.add_parser("company", help="Manage companies")
    company_sub = company.add_subparsers(dest="company_command", required=True)
    company_sub.add_parser("add", help="Add a company")
    list_parser = company_sub.add_parser("list", help="List companies")
    list_parser.add_argument("--status")
    contact_group = list_parser.add_mutually_exclusive_group()
    contact_group.add_argument(
        "--with-contact", dest="has_contact", action="store_true"
    )
    contact_group.add_argument(
        "--without-contact", dest="has_contact", action="store_false"
    )
    list_parser.set_defaults(has_contact=None)
    show = company_sub.add_parser("show", help="Show a company")
    show.add_argument("id", type=int)
    for command in ("queue", "sent", "replied", "failed", "block"):
        action = company_sub.add_parser(command, help=f"Mark a company {command}")
        action.add_argument("id", type=int)
    importer = company_sub.add_parser("import", help="Import companies from CSV")
    importer.add_argument("csv_path")


def _add_ingest_commands(sub: argparse._SubParsersAction) -> None:
    ingest = sub.add_parser("ingest", help="Ingest companies from source files")
    ingest_sub = ingest.add_subparsers(dest="ingest_command", required=True)
    for command, help_text in (
        ("csv", "Import a company CSV file"),
        ("domains", "Import a domain-per-line text file"),
        ("json", "Import structured company JSON"),
        ("sec", "Import the SEC company_tickers.json dataset"),
    ):
        source = ingest_sub.add_parser(command, help=help_text)
        source.add_argument("path")
        source.add_argument(
            "--filter-profile",
            action="store_true",
            help="Apply conservative filtering using the saved candidate profile",
        )
    ingest_sub.add_parser("history", help="Show ingestion history")


def _add_contact_commands(sub: argparse._SubParsersAction) -> None:
    contacts = sub.add_parser("contacts", help="Discover public contact routes")
    contacts_sub = contacts.add_subparsers(dest="contacts_command", required=True)
    discover = contacts_sub.add_parser("discover", help="Discover one company")
    discover.add_argument("id", type=int)
    discover.add_argument("--page-budget", type=int, default=4)
    discover.add_argument("--delay", type=float, default=0.25)
    discover_all = contacts_sub.add_parser(
        "discover-all", help="Discover a bounded company batch"
    )
    discover_all.add_argument("--limit", type=int, default=50)
    discover_all.add_argument("--retry-failed", action="store_true")
    discover_all.add_argument("--page-budget", type=int, default=4)
    discover_all.add_argument("--delay", type=float, default=0.25)
    discover_all.add_argument("--company-delay", type=float, default=0.5)
    for command in ("list", "best"):
        parser = contacts_sub.add_parser(command, help=f"{command.title()} contact routes")
        parser.add_argument("id", type=int)
    contacts_sub.add_parser("stats", help="Show contact discovery statistics")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="outreach")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init", help="Answer onboarding questions and save your profile")
    sub.add_parser("show", help="Print your saved profile")
    _add_company_commands(sub)
    _add_ingest_commands(sub)
    _add_contact_commands(sub)
    queue = sub.add_parser("queue", help="Manage the outreach queue")
    queue_sub = queue.add_subparsers(dest="queue_command", required=True)
    queue_sub.add_parser("list", help="List queued companies")
    sub.add_parser("stats", help="Show company statistics")
    args = parser.parse_args(argv)

    if args.command == "init":
        return cmd_init()
    if args.command == "show":
        return cmd_show_profile()
    if args.command == "stats":
        return cmd_stats()
    if args.command == "queue":
        return cmd_queue_list()
    if args.command == "ingest":
        return cmd_ingest(args)
    if args.command == "contacts":
        return cmd_contacts(args)
    if args.company_command == "add":
        return cmd_company_add()
    if args.company_command == "list":
        return cmd_company_list(args)
    if args.company_command == "show":
        return cmd_company_show(args.id)
    if args.company_command == "import":
        return cmd_company_import(args.csv_path)
    statuses = {
        "queue": "queued",
        "sent": "sent",
        "replied": "replied",
        "failed": "failed",
        "block": "do_not_contact",
    }
    return cmd_company_status(args.id, statuses[args.company_command])
