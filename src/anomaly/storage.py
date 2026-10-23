"""Historique des métriques : base SQLite (une ligne par métrique et instant) et fichiers CSV."""

import csv
import json
import sqlite3
from collections.abc import Iterable, Sequence
from datetime import datetime
from pathlib import Path
from typing import Self

from anomaly.model import Kind, Point

DEFAULT_DB = Path("data/history.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS points (
    metric    TEXT NOT NULL,
    timestamp TEXT NOT NULL,  -- ISO 8601: sorting the text sorts the dates
    value     REAL NOT NULL,
    label     TEXT,           -- ground truth when known (spike, drop, drift), NULL otherwise
    PRIMARY KEY (metric, timestamp)
)
"""

# La clé primaire interdit deux lignes pour la même métrique et le même instant :
# ré-importer un point le remplace au lieu de créer un doublon.
UPSERT = """
INSERT INTO points (metric, timestamp, value, label) VALUES (?, ?, ?, ?)
ON CONFLICT (metric, timestamp) DO UPDATE SET value = excluded.value, label = excluded.label
"""

# Une borne absente devient '' ou '9999', que tout horodatage ISO dépasse ou n'atteint pas.
SELECT_RANGE = """
SELECT timestamp, value, label FROM points
WHERE metric = ? AND timestamp >= coalesce(?, '') AND timestamp < coalesce(?, '9999')
ORDER BY timestamp
"""


def to_text(timestamp: datetime) -> str:
    return timestamp.isoformat(timespec="seconds")


class History:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path)
        self.connection.execute(SCHEMA)

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.connection.close()

    def add(self, metric: str, points: Iterable[Point]) -> int:
        """Stocke les points, en remplaçant ceux du même instant. Renvoie le nombre de nouveaux."""
        before = self.count(metric)
        rows = (
            (metric, to_text(p.timestamp), p.value, p.label.value if p.label else None)
            for p in points
        )
        with self.connection:  # une seule transaction : tous les points sont stockés, ou aucun
            self.connection.executemany(UPSERT, rows)
        return self.count(metric) - before

    def read(
        self, metric: str, start: datetime | None = None, end: datetime | None = None
    ) -> list[Point]:
        """Points de `metric` de `start` (inclus) à `end` (exclu), du plus ancien au plus récent."""
        bounds = (to_text(start) if start else None, to_text(end) if end else None)
        rows = self.connection.execute(SELECT_RANGE, (metric, *bounds))
        return [
            Point(datetime.fromisoformat(timestamp), value, Kind(label) if label else None)
            for timestamp, value, label in rows
        ]

    def count(self, metric: str) -> int:
        query = "SELECT count(*) FROM points WHERE metric = ?"
        count: int = self.connection.execute(query, (metric,)).fetchone()[0]
        return count

    def metrics(self) -> list[str]:
        rows = self.connection.execute("SELECT DISTINCT metric FROM points ORDER BY metric")
        return [metric for (metric,) in rows]


def write_csv(points: Iterable[Point], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as file:
        writer = csv.writer(file, lineterminator="\n")  # par défaut \r\n, ce qui casse awk/grep
        writer.writerow(["timestamp", "value", "label"])
        for point in points:
            timestamp = point.timestamp.isoformat(timespec="minutes")
            writer.writerow([timestamp, point.value, point.label or ""])


def read_csv(path: Path) -> list[Point]:
    """Lit `timestamp,value[,label]` : CSV du simulateur, ou données réelles sans étiquette."""
    with path.open(newline="") as file:
        reader = csv.DictReader(file)
        missing = {"timestamp", "value"} - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f"{path}: missing column(s) {', '.join(sorted(missing))}")
        points = []
        for line, row in enumerate(reader, start=2):  # la ligne 1 est l'en-tête
            try:
                label = row.get("label")
                timestamp = datetime.fromisoformat(row["timestamp"])
                points.append(Point(timestamp, float(row["value"]), Kind(label) if label else None))
            except ValueError as error:
                raise ValueError(f"{path}:{line}: {error}") from error
    return points


def read_windows(path: Path, csv_name: str) -> list[tuple[datetime, datetime]]:
    """Fenêtres d'anomalies d'un fichier NAB, lues dans `labels/combined_windows.json`.

    Le JSON associe chaque fichier (`realKnownCause/nyc_taxi.csv`) à ses fenêtres [début, fin].
    """
    with path.open() as file:
        windows: dict[str, list[list[str]]] = json.load(file)
    for name, spans in windows.items():
        if name.rsplit("/", 1)[-1] == csv_name:
            return [(datetime.fromisoformat(a), datetime.fromisoformat(b)) for a, b in spans]
    raise ValueError(f"{path}: no anomaly windows for {csv_name}")


def label_windows(
    points: Sequence[Point], windows: Sequence[tuple[datetime, datetime]]
) -> list[Point]:
    """Étiquette `anomaly` les points dans une fenêtre, début et fin inclus ; les autres restent."""
    return [
        Point(point.timestamp, point.value, Kind.ANOMALY)
        if any(start <= point.timestamp <= end for start, end in windows)
        else point
        for point in points
    ]
