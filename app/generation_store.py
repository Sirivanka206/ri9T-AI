import json
import os
import tempfile
from pathlib import Path
from typing import Any


class JsonGenerationStore:
    """Small local document store: one immutable JSON file per successful generation."""

    def __init__(self, directory: str | Path):
        self.directory = Path(directory)

    def _paths(self) -> list[Path]:
        if not self.directory.exists():
            return []
        return sorted(self.directory.glob("*.json"))

    def all(self) -> list[dict[str, Any]]:
        records = []
        for path in self._paths():
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise RuntimeError(f"Could not read generation record {path.name}: {exc}") from exc
            if not isinstance(value, dict) or "generation_id" not in value:
                raise RuntimeError(f"Generation record {path.name} has an invalid shape.")
            records.append(value)
        return records

    def save(self, record: dict[str, Any]) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        destination = self.directory / f"{record['generation_id']}.json"
        if destination.exists():
            raise FileExistsError(f"Generation record already exists: {destination.name}")
        handle, temporary_path = tempfile.mkstemp(
            prefix=".generation-", suffix=".tmp", dir=self.directory
        )
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as temporary_file:
                json.dump(record, temporary_file, ensure_ascii=False, indent=2)
                temporary_file.flush()
                os.fsync(temporary_file.fileno())
            os.replace(temporary_path, destination)
        except Exception:
            if os.path.exists(temporary_path):
                os.remove(temporary_path)
            raise

    def get(self, generation_id: str) -> dict[str, Any] | None:
        path = self.directory / f"{generation_id}.json"
        if not path.exists():
            return None
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"Could not read generation record {path.name}: {exc}") from exc
        if not isinstance(value, dict):
            raise RuntimeError(f"Generation record {path.name} has an invalid shape.")
        return value

    def find_reusable(
        self, selection_id: str, model: str, prompt_version: str
    ) -> dict[str, Any] | None:
        for record in self.all():
            if (
                record.get("selection_id") == selection_id
                and record.get("model") == model
                and record.get("prompt_version") == prompt_version
            ):
                return record
        return None
