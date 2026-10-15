"""Data shared by every module: a metric point and the kinds of anomaly."""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class Kind(StrEnum):
    SPIKE = "spike"
    DROP = "drop"
    DRIFT = "drift"


@dataclass(frozen=True)
class Point:
    timestamp: datetime  # naive local time: the day/night cycle is local
    value: float
    label: Kind | None  # ground truth: the anomaly at this point, None when normal or unknown
