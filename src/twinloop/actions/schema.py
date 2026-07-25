from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError


class BaseAction(BaseModel):
    model_config = ConfigDict(extra="forbid")


class MigrateService(BaseAction):
    type: Literal["migrate_service"] = "migrate_service"
    service_id: str
    target_node_id: str


class RestartService(BaseAction):
    type: Literal["restart_service"] = "restart_service"
    service_id: str


class ScaleService(BaseAction):
    type: Literal["scale_service"] = "scale_service"
    service_id: str
    delta_replicas: int


class RerouteTraffic(BaseAction):
    type: Literal["reroute_traffic"] = "reroute_traffic"
    service_id: str
    path_hint: list[str]


class ThrottleService(BaseAction):
    type: Literal["throttle_service"] = "throttle_service"
    service_id: str
    rate_limit: float


class NoOp(BaseAction):
    type: Literal["no_op"] = "no_op"


Action = Annotated[
    Union[
        MigrateService,
        RestartService,
        ScaleService,
        RerouteTraffic,
        ThrottleService,
        NoOp,
    ],
    Field(discriminator="type"),
]

_ADAPTER = TypeAdapter(Action)


@dataclass
class ParseError:
    message: str


def parse_action(data) -> "Union[BaseAction, ParseError]":
    try:
        return _ADAPTER.validate_python(data)
    except ValidationError as error:
        return ParseError(message=str(error))
