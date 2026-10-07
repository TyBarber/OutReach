import argparse
import json
from pathlib import Path

from outreach.config import profile_path
from outreach.database import (
    DuplicateCompanyError,
    add_company,
    get_company,
    get_outreach_queue,
    get_stats,
    import_companies_csv,
    list_companies,
    set_company_status,
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
    }
    stats = get_stats()
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="outreach")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init", help="Answer onboarding questions and save your profile")
    sub.add_parser("show", help="Print your saved profile")
    _add_company_commands(sub)
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
