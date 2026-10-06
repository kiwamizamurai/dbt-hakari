"""Where collected data and results live on disk. The only place that reads and writes files."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import TypeVar

from pydantic import ValidationError

from dbt_hakari.config import describe
from dbt_hakari.costdata import CostData
from dbt_hakari.errors import UsageError
from dbt_hakari.history import History
from dbt_hakari.models import Model
from dbt_hakari.optimize import Plan
from dbt_hakari.verify import VerificationReport

M = TypeVar("M", bound=Model)


@dataclass(frozen=True)
class DataStore:
    directory: Path

    def _write(self, name: str, model: Model) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(model.model_dump(mode="json"), indent=1, sort_keys=True)
        (self.directory / name).write_text(payload)

    def _read(self, name: str, model: type[M]) -> M | None:
        path = self.directory / name
        if not path.exists():
            return None
        try:
            return model.model_validate_json(path.read_text())
        except OSError as error:
            raise UsageError(f"cannot read {path}: {error}") from error
        except ValidationError as error:
            raise UsageError(
                f"{path} cannot be read ({describe(error)}). It is damaged, or written by "
                "another version of dbt-hakari: run `dbt-hakari collect` again"
            ) from error

    def save_cost_data(self, cost_data: CostData) -> None:
        self._write("costdata.json", cost_data)

    def save_history(self, history: History) -> None:
        self._write("history.json", history)

    def save_verification(self, report: VerificationReport) -> None:
        self._write("verification.json", report)

    def save_plan(self, plan: Plan) -> None:
        self._write("plan.json", plan)

    def load_cost_data(self) -> CostData:
        cost_data = self._read("costdata.json", CostData)
        if cost_data is None:
            raise UsageError(
                f"{self.directory / 'costdata.json'} not found: run `dbt-hakari collect` first"
            )
        return cost_data

    def load_verification(self) -> VerificationReport | None:
        return self._read("verification.json", VerificationReport)

    def load_plan(self) -> Plan | None:
        return self._read("plan.json", Plan)

    def load_history(self) -> History | None:
        return self._read("history.json", History)
