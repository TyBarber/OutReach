import argparse
import json

from outreach.config import profile_path
from outreach.onboarding import run_onboarding
from outreach.store import load_profile, save_profile

def cmd_init() -> int:
    profile = run_onboarding()
    save_profile(profile, profile_path())
    print(f"\nSaved profile to {profile_path()}")
    return 0


def cmd_show() -> int:
    try:
        profile = load_profile(profile_path())
    except FileNotFoundError as err:
        print(err)
        return 1
    print(json.dumps(profile.to_dict(), indent=2))
    return 0


def main(argv: list[str] | None = None) -> int: 
    parser = argparse.ArgumentParser(prog="outreach")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init", help="Answer onboarding questions and save your profile")
    sub.add_parser("show", help="Print your saved profile")
    args = parser.parse_args(argv)

    if args.command == "init":
        return cmd_init()
    return cmd_show()