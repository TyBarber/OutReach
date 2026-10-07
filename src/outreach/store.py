import json 
from pathlib import Path

from outreach.models import Profile


def save_profile(profile: Profile, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(profile.to_dict(), indent=2), encoding="utf-8")


def load_profile(path: Path) -> Profile:
    if not path.exists():
        raise FileNotFoundError(f"No profile at {path}. Run 'outreach init' first.")
    data = json.loads(path.read_text(encoding="utf-8"))
    return Profile.from_dict(data)