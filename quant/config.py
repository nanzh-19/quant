from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    import yaml
except ModuleNotFoundError as exc:
    raise ModuleNotFoundError(
        "Missing dependency 'PyYAML'. Install project dependencies first, for example: "
        "`python3 -m pip install -r requirements.txt`."
    ) from exc


@dataclass
class AppConfig:
    raw: dict[str, Any]
    root: Path

    def section(self, name: str) -> dict[str, Any]:
        return self.raw.get(name, {})


def load_config(path: str | Path | None = None) -> AppConfig:
    config_path = Path(path or "config/config.yml")
    if not config_path.exists():
        config_path = Path("config/config.example.yml")
    with config_path.open("r", encoding="utf-8") as handle:
        raw = yaml.safe_load(handle) or {}
    return AppConfig(raw=raw, root=Path.cwd())
