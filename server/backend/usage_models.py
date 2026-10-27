"""Strict date-only daily usage contract; Beijing UI uses a fixed UTC+8 day."""

import re
from datetime import date, datetime, time, timedelta, timezone

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, field_validator, model_validator

BEIJING = timezone(timedelta(hours=8), name="Asia/Shanghai")
MAX_DAYS = 366


def today() -> date:
    return datetime.now(BEIJING).date()


def midnight(day: date) -> datetime:
    return datetime.combine(day, time(), tzinfo=BEIJING)


class UsageRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    projectId: str | None = Field(default=None, max_length=128)
    apiKeyId: str | None = Field(default=None, max_length=256)
    startDate: str | None = Field(default=None, max_length=10)
    endDate: str | None = Field(default=None, max_length=10)
    includeDailyDetails: bool = True
    _resolved_bounds: tuple[date, date] | None = PrivateAttr(default=None)

    @field_validator("projectId", "apiKeyId", mode="before")
    @classmethod
    def empty_strings(cls, value):
        return value.strip() or None if isinstance(value, str) else value

    @field_validator("startDate", "endDate", mode="before")
    @classmethod
    def blank_dates(cls, value):
        return None if isinstance(value, str) and not value.strip() else value

    @field_validator("startDate", "endDate")
    @classmethod
    def date_only(cls, value):
        if value is not None:
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
                raise ValueError("Expected YYYY-MM-DD")
            date.fromisoformat(value)
        return value

    @model_validator(mode="after")
    def validate_range(self):
        self._resolved_bounds = self.bounds()
        return self

    def bounds(self) -> tuple[date, date]:
        if self._resolved_bounds is not None:
            return self._resolved_bounds
        current = today()
        start = date.fromisoformat(self.startDate) if self.startDate else current.replace(day=1)
        end = date.fromisoformat(self.endDate) if self.endDate else current
        if not 0 <= (end - start).days < MAX_DAYS:
            raise ValueError("Expected ordered date range of at most 366 days")
        try:
            # Reject dates whose exclusive upper bound or UTC conversion overflows.
            midnight(start).astimezone(timezone.utc)
            midnight(end + timedelta(days=1)).astimezone(timezone.utc)
        except OverflowError as exc:
            raise ValueError("Date boundary out of range") from exc
        return start, end
