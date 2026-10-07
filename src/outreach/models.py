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
ContactRouteType = Literal[
    "recruiting_email",
    "careers_email",
    "talent_email",
    "hiring_email",
    "hr_email",
    "general_email",
    "careers_form",
    "contact_form",
    "careers_page",
    "contact_page",
    "other",
]
ContactRouteStatus = Literal["active", "invalid", "stale", "blocked", "unknown"]
DiscoveryStatus = Literal["pending", "success", "no_routes", "failed"]

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
CONTACT_ROUTE_TYPES = (
    "recruiting_email",
    "careers_email",
    "talent_email",
    "hiring_email",
    "hr_email",
    "general_email",
    "careers_form",
    "contact_form",
    "careers_page",
    "contact_page",
    "other",
)
CONTACT_ROUTE_STATUSES = ("active", "invalid", "stale", "blocked", "unknown")
DISCOVERY_STATUSES = ("pending", "success", "no_routes", "failed")


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


@dataclass
class ContactRoute:
    company_id: int
    route_type: ContactRouteType
    value: str
    id: int | None = None
    source_url: str | None = None
    source_type: str = "public_website"
    confidence: float = 0.5
    verified: bool = False
    discovered_at: datetime = field(default_factory=utc_now)
    last_checked_at: datetime = field(default_factory=utc_now)
    status: ContactRouteStatus = "active"
    notes: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ContactDiscoveryState:
    company_id: int
    last_discovery_at: datetime
    discovery_status: DiscoveryStatus
    pages_checked: int = 0
    routes_found: int = 0
    error_message: str | None = None
