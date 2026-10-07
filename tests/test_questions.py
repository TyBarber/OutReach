from dataclasses import fields
import profile

import pytest

from outreach.models import Profile
from outreach.questions import QUESTIONS, Question, parse_answer


def test_every_question_maps_to_a_profile_field():
    assert {q.key for q in QUESTIONS} == {f.name for f in fields(Profile)}

def test_text_is_stripped():
    assert parse_answer(Question("name", "?"), " Ty ") == "Ty"


def test_required_rejects_blank():
    with pytest.raises(ValueError):
        parse_answer(Question("name", "?"), "  ")


def test_optional_blank_returns_none():
    q = Question("resume_path", "?", kind="path", required=False)
    assert parse_answer(q, "") is None

def test_list_splits_and_trims():
    q = Question("skills", "?", kind="list")
    assert parse_answer(q, "linux, bash ,, aws") == ["linux", "bash", "aws"]

def test_choice_is_case_insensitive_and_canonical():
    q = Question("tone", "?", kind="choice", choices=("formal", "warm"))
    assert parse_answer(q, "WARM") == "warm"


def test_choice_rejects_unknown():
    q = Question("tone", "?", kind="choice", choices=("formal", "warm"))
    with pytest.raises(ValueError):
        parse_answer(q, "silly")

def test_path_must_exist(tmp_path):
    q = Question("resume_path", "?", kind="path")
    with pytest.raises(ValueError):
        parse_answer(q, str(tmp_path / "missing.pdf"))
    f = tmp_path / "cv.pdf"
    f.write_text("x")
    assert parse_answer(q, str(f)) == str(f)
