"""Base classes for everything that crosses the boundary of the process (files, JSON output).

Unknown keys are rejected, so a typo in a config file or a file written by a different version
is reported instead of being silently ignored.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FrozenModel(Model):
    model_config = ConfigDict(extra="forbid", frozen=True)
