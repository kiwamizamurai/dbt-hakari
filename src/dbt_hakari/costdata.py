"""What ``collect`` learns from BigQuery: bytes processed and referenced-table counts."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from dbt_hakari.models import FrozenModel, Model
from dbt_hakari.units import Bytes, Count


class NodeCost(FrozenModel):
    uid: str
    bytes_processed: Bytes
    n_tables: Count
    referenced: tuple[str, ...] = ()
    error: str | None = None


class CostData(Model):
    schema_version: Literal[1] = 1
    project: str | None = None
    location: str | None = None
    collected_at: str | None = None
    manifest_sha256: str | None = None
    nodes: dict[str, NodeCost] = Field(default_factory=dict)
    leaf_weight: dict[str, int] = Field(default_factory=dict)
    leaf_bytes: dict[str, Bytes] = Field(default_factory=dict)  # size of each table that exists
