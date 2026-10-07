from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

Kind = Literal["text", "list", "choice", "path"]


@dataclass(frozen=True)
class Question:
    key: str
    prompt: str
    kind: Kind = "text"
    required: bool = True
    choices: tuple[str, ...] = ()
    hint: str = ""


QUESTIONS: tuple[Question, ...] = (
    Question("name", "What's your full name?"), 
    Question("education", "Highest education (degree, school)?"),
    Question("field_of_study", "What's your field?"), 
    Question("target_roles", "What roles are you targeting?", 
              kind="list", hint="comma-separated"),
    Question("skills", "Top skills or tools to mention?",
             kind="list", hint="comma-separated"),
    Question("work_styles", "Preferred work styles?", 
             kind="list", hint="e.g. remote, async, small team"),
    Question("location", "Where are you based?"),
    Question("tone", "Email tone?", 
             kind="choice", choices=("formal", "warm", "direct")),
    Question("resume_path", "Path to your resume?", 
             kind="path", required=False, hint="Enter to skip"),
)


def parse_answer(question: Question, raw: str) -> Any:
    """ Validate raw input and convert it to the right type. 
    
    Raises ValueError with a user-readable message if invalid.
    """
    
    raw = raw.strip()

    if not raw:
        if question.required:
            raise ValueError("This question is required.")
        return [] if question.kind == "list" else None

    if question.kind == "text":
        return raw

    if question.kind == "list":
        return [item.strip() for item in raw.split(",") if item.strip()]

    if question.kind == "choice":
        for choice in question.choices:
            if raw.lower() == choice.lower():
                return choice
        options = ", ".join(question.choices)
        raise ValueError(f"Pick one of : {options}.")

    if question.kind == "path":
        path = Path(raw).expanduser()
        if not path.is_file():
            raise ValueError(f"No file found at {path}.")
        return str(path)

    raise ValueError(f"Unknown question kind: {question.kind}")
    