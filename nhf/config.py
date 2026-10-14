"""Loading of the YAML config files.

Everything tunable lives in config/*.yml so a change of mind or a change of
policy is a one-line edit, not a code change.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = Path(os.environ.get("NHF_CONFIG_DIR", REPO_ROOT / "config"))
DATA_DIR = Path(os.environ.get("NHF_DATA_DIR", REPO_ROOT / "data"))
DB_PATH = Path(os.environ.get("NHF_DB", DATA_DIR / "nhf.db"))

# Downloads land here and nowhere else. Keeping them beside the database
# put a 174 MB GeoJSON in `data/`, which a `git add -A` then committed and
# GitHub refused — it blocks files over 100 MB. One ignored directory is
# easier to keep right than a list of extensions.
CACHE_DIR = Path(os.environ.get("NHF_CACHE_DIR", DATA_DIR / "cache"))

TODO = "TODO"


def _load(name: str) -> dict[str, Any]:
    path = CONFIG_DIR / name
    with path.open() as fh:
        return yaml.safe_load(fh) or {}


@dataclass(frozen=True)
class Office:
    code: str
    label: str
    postal: str


@dataclass(frozen=True)
class Config:
    datasets: dict[str, Any]
    criteria: dict[str, Any]
    policy: dict[str, Any]
    future_mrt: dict[str, Any]

    # --- shortcuts the rest of the code reads constantly -----------------
    @property
    def household(self) -> dict[str, Any]:
        return self.criteria["household"]

    @property
    def weights(self) -> dict[str, float]:
        return dict(self.criteria["weights"])

    @property
    def growth_weights(self) -> dict[str, float]:
        return dict(self.criteria["growth_weights"])

    @property
    def fair_value(self) -> dict[str, Any]:
        return self.criteria["fair_value"]

    @property
    def growth(self) -> dict[str, Any]:
        return self.criteria["growth"]

    @property
    def flags(self) -> dict[str, Any]:
        return self.criteria["criteria"]

    @property
    def offices(self) -> list[Office]:
        return [Office(**o) for o in self.household["offices"]]

    @property
    def budget(self) -> float:
        return float(self.household["budget"])

    @property
    def holding_years(self) -> int:
        return int(self.household["holding_period_years"])

    def api(self, key: str) -> Any:
        return self.datasets["api"][key]

    def onemap(self, key: str) -> Any:
        return self.datasets["onemap"][key]

    def resource_ids(self, key: str) -> list[dict[str, Any]]:
        """Dataset entries for `key`, as a list, TODO placeholders included.

        Callers check `entry["id"] != TODO` so an unconfirmed id degrades to a
        skipped dataset with a message, never to a silent wrong answer.
        """
        entry = self.datasets[key]
        return list(entry) if isinstance(entry, list) else [entry]


@lru_cache(maxsize=1)
def load_config() -> Config:
    return Config(
        datasets=_load("datasets.yml"),
        criteria=_load("criteria.yml"),
        policy=_load("policy.yml"),
        future_mrt=_load("future_mrt.yml"),
    )
