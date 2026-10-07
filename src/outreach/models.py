from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal


ContactType = Literal[
    "recruiting",
    "careers",
    "talent",
    "hr",
    "hiring",
    "general",
    "other",
]
CompanyStatus = Literal[
    "discovered",
    "contact_found",
    "queued",
    "sent",
    "replied",
    "failed",
    "do_not_contact",
]

CONTACT_TYPES = (
    "recruiting",
    "careers",
    "talent",
    "hr",
    "hiring",
    "general",
    "other",
)
COMPANY_STATUSES = (
    "discovered",
    "contact_found",
    "queued",
    "sent",
    "replied",
    "failed",
    "do_not_contact",
)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


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

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Profile":
        return cls(**data)


@dataclass
class Company:
    name: str
    domain: str | None = None
    id: int | None = None
    website: str | None = None
    careers_url: str | None = None
    contact_email: str | None = None
    contact_type: ContactType | None = None
    status: CompanyStatus = "discovered"
    last_contacted_at: datetime | None = None
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
