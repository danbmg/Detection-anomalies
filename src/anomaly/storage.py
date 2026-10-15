"""Metric history: SQLite database (one row per metric and timestamp) and CSV files."""

import csv
import sqlite3
from collections.abc import Iterable
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

# The primary key forbids two rows for the same metric and timestamp:
# importing a point again replaces it instead of creating a duplicate.
UPSERT = """
INSERT INTO points (metric, timestamp, value, label) VALUES (?, ?, ?, ?)
ON CONFLICT (metric, timestamp) DO UPDATE SET value = excluded.value, label = excluded.label
"""

# Missing bounds become '' and '9999', which every ISO timestamp is above and below.
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
        """Store points, replacing those stored at the same timestamps. Returns the new ones."""
        before = self.count(metric)
        rows = (
            (metric, to_text(p.timestamp), p.value, p.label.value if p.label else None)
            for p in points
        )
        with self.connection:  # one transaction: every point is stored, or none
            self.connection.executemany(UPSERT, rows)
        return self.count(metric) - before

    def read(
        self, metric: str, start: datetime | None = None, end: datetime | None = None
    ) -> list[Point]:
        """Points of `metric` from `start` (included) to `end` (excluded), oldest first."""
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
        writer = csv.writer(file, lineterminator="\n")  # default is \r\n, which breaks awk/grep
        writer.writerow(["timestamp", "value", "label"])
        for point in points:
            timestamp = point.timestamp.isoformat(timespec="minutes")
            writer.writerow([timestamp, point.value, point.label or ""])


def read_csv(path: Path) -> list[Point]:
    """Read `timestamp,value[,label]` rows: the simulator's CSV, or real data without labels."""
    with path.open(newline="") as file:
        reader = csv.DictReader(file)
        missing = {"timestamp", "value"} - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f"{path}: missing column(s) {', '.join(sorted(missing))}")
        points = []
        for line, row in enumerate(reader, start=2):  # line 1 is the header
            try:
                label = row.get("label")
                timestamp = datetime.fromisoformat(row["timestamp"])
                points.append(Point(timestamp, float(row["value"]), Kind(label) if label else None))
            except ValueError as error:
                raise ValueError(f"{path}:{line}: {error}") from error
    return points
