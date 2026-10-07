from dataclasses import asdict, dataclass
from typing import Any


@dataclass

class Profile:
    name: str
    education: str
    field_of_study: str
    target_roles: list[str]
    skills: list[str]
    work_styles: list[str]
    location: str
    tone: str
    resume_path: str | None = None

    def to_dict(self) -> dict[str,Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Profile":
        return cls(**data)