import os
from pathlib import Path

from platformdirs import user_data_dir

APP_NAME = "outreach"


def data_dir() -> Path:
    override = os.environ.get("OUTREACH_DATA_DIR")
    path = Path(override) if override else Path(user_data_dir(APP_NAME))
    path.mkdir(parents=True, exist_ok=True)
    return path

def profile_path() -> Path:
    return data_dir() / "profile.json"