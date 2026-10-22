"""Request and response shapes. Pydantic validates everything the client sends."""

from datetime import date, datetime, time
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, StringConstraints, field_validator

from app.timeutil import is_valid_zone

Str = Annotated[str, StringConstraints(strip_whitespace=True)]

# The fixed reminder choices the interface offers (minutes before the deadline).
REMINDER_CHOICES = {60, 180, 1440, 2880, 10080}

COURSE_COLORS = [
    "#2563eb",  # blue
    "#0d9488",  # teal
    "#16a34a",  # green
    "#ca8a04",  # amber
    "#ea580c",  # orange
    "#dc2626",  # red
    "#db2777",  # pink
    "#7c3aed",  # violet
    "#475569",  # slate
]


def _check_zone(v: str) -> str:
    if not is_valid_zone(v):
        raise ValueError("Choose a valid time zone, such as America/New_York.")
    return v


# --- Auth -----------------------------------------------------------------------


class Credentials(BaseModel):
    email: EmailStr = Field(max_length=254)
    password: str = Field(min_length=1, max_length=128)


class Registration(BaseModel):
    email: EmailStr = Field(max_length=254)
    password: str = Field(min_length=10, max_length=128)
    time_zone: str = "America/New_York"

    _zone = field_validator("time_zone")(_check_zone)


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    email: str
    time_zone: str


class SessionOut(BaseModel):
    user: UserOut | None
    csrf_token: str | None


class SettingsUpdate(BaseModel):
    time_zone: str

    _zone = field_validator("time_zone")(_check_zone)


class PasswordChange(BaseModel):
    current_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=10, max_length=128)


class AccountDeletion(BaseModel):
    password: str = Field(min_length=1, max_length=128)


# --- Courses --------------------------------------------------------------------


class CourseFields(BaseModel):
    name: Annotated[Str, StringConstraints(min_length=1, max_length=120)]
    code: Annotated[Str, StringConstraints(max_length=40)] | None = None
    instructor: Annotated[Str, StringConstraints(max_length=120)] | None = None
    color: str = COURSE_COLORS[0]

    @field_validator("code", "instructor")
    @classmethod
    def _blank_to_none(cls, v: str | None) -> str | None:
        return v or None

    @field_validator("color")
    @classmethod
    def _color(cls, v: str) -> str:
        v = v.lower()
        if v not in COURSE_COLORS:
            raise ValueError("Choose one of the course colors.")
        return v


class CourseCreate(CourseFields):
    pass


class CourseUpdate(CourseFields):
    archived: bool = False


class CourseOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    code: str | None
    instructor: str | None
    color: str
    archived: bool
    open_assignments: int = 0


class DeletionImpact(BaseModel):
    assignments: int


# --- Assignments ----------------------------------------------------------------


class AssignmentFields(BaseModel):
    title: Annotated[Str, StringConstraints(min_length=1, max_length=200)]
    course_id: int
    description: Annotated[Str, StringConstraints(max_length=5000)] | None = None
    due_date: date
    # Local wall-clock time in the user's time zone. Omit for a date-only deadline.
    due_time: time | None = None
    reminder_minutes: int | None = 1440
    # Set after the user has seen the likely-duplicate warning and chooses to save anyway.
    allow_duplicate: bool = False

    @field_validator("description")
    @classmethod
    def _blank_to_none(cls, v: str | None) -> str | None:
        return v or None

    @field_validator("due_time")
    @classmethod
    def _minute_precision(cls, v: time | None) -> time | None:
        return v.replace(second=0, microsecond=0) if v else None

    @field_validator("due_date")
    @classmethod
    def _reasonable_year(cls, v: date) -> date:
        if not 2000 <= v.year <= 2100:
            raise ValueError("Choose a date between 2000 and 2100.")
        return v

    @field_validator("reminder_minutes")
    @classmethod
    def _reminder(cls, v: int | None) -> int | None:
        if v is not None and v not in REMINDER_CHOICES:
            raise ValueError("Choose one of the reminder options.")
        return v


class AssignmentCreate(AssignmentFields):
    pass


class AssignmentUpdate(AssignmentFields):
    pass


class AssignmentCompletion(BaseModel):
    completed: bool


class CourseBrief(BaseModel):
    id: int
    name: str
    code: str | None
    color: str
    archived: bool


class AssignmentOut(BaseModel):
    id: int
    title: str
    description: str | None
    course: CourseBrief
    # How the deadline looks in the user's current time zone:
    due_date: date  # local calendar date
    due_time: time | None  # local time, None for date-only
    due_at: datetime | None  # exact UTC instant, None for date-only
    all_day: bool
    deadline: datetime  # the instant it becomes overdue (UTC)
    overdue: bool
    completed: bool
    completed_at: datetime | None
    reminder_minutes: int | None
    time_note: Literal["ambiguous_earlier"] | None = None


class DuplicateWarning(BaseModel):
    code: Literal["likely_duplicate"] = "likely_duplicate"
    message: str
    matches: list[AssignmentOut]


class TodayOut(BaseModel):
    today: date
    time_zone: str
    overdue: list[AssignmentOut]
    due_today: list[AssignmentOut]
    upcoming: list[AssignmentOut]
