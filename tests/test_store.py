import pytest

from outreach.models import Profile
from outreach.store import load_profile, save_profile


def make_profile() -> Profile:
    return Profile(
        name="Ty", 
        education="BSc, Example UNI", 
        field_of_study="Computer Science",
        target_roles=["Software Engineer", "DevOps Engineer"],
        skills=["Python", "Linux", "AWS"],
        work_styles=["Remote", "Team-oriented"],
        location="San Francisco, CA",
        tone="warm",
    )


def test_round_trip(tmp_path):
    path = tmp_path / "profile.json"
    original = make_profile()
    save_profile(original, path)
    assert load_profile(path) == original


def test_load_missing_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_profile(tmp_path / "nope.json")
        