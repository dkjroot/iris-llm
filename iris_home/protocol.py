"""Shared types for the home service and the conversational skill."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

PowerState = Literal["on", "off"]
Metric = Literal["power", "energy", "voltage", "current"]
Day = Literal["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
DAYS: tuple[Day, ...] = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
DeviceName = Annotated[str, Field(min_length=1, max_length=80)]


class Request(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class DeviceRequest(Request):
    device: DeviceName


class SwitchRequest(DeviceRequest):
    state: PowerState


class MeterRequest(DeviceRequest):
    metric: Metric = "power"


class Rule(Request):
    days: list[Day] = Field(min_length=1, max_length=7)
    time: str = Field(pattern=r"^(?:[01][0-9]|2[0-3]):[0-5][0-9]$", description="Local HH:MM.")
    state: PowerState


class ScheduleRequest(DeviceRequest):
    revision: int = Field(ge=0, description="Revision returned by get_home_schedule.")
    rules: list[Rule] = Field(max_length=32, description="The complete weekly schedule.")

    @model_validator(mode="after")
    def unique_slots(self) -> "ScheduleRequest":
        slots = [(day, rule.time) for rule in self.rules for day in rule.days]
        if len(set(slots)) != len(slots):
            raise ValueError("Each weekday and time can appear only once")
        return self


OPERATIONS: dict[str, type[Request]] = {
    "list": Request,
    "status": DeviceRequest,
    "switch": SwitchRequest,
    "meter": MeterRequest,
    "schedule": DeviceRequest,
    "save-schedule": ScheduleRequest,
}
