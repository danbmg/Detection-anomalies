"""Données partagées par tous les modules : un point de métrique et les types d'anomalie."""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class Kind(StrEnum):
    SPIKE = "spike"
    DROP = "drop"
    DRIFT = "drift"


@dataclass(frozen=True)
class Point:
    timestamp: datetime  # heure locale sans fuseau : le cycle jour/nuit est local
    value: float
    label: Kind | None  # vérité terrain : l'anomalie à ce point, None si normal ou inconnu
