"""Exclusion patterns: ``tag:x``, ``path:models/a/*``, ``uid:model.pkg.*``, ``name:int_*``
or a bare glob that matches the node name."""

from __future__ import annotations

from fnmatch import fnmatchcase

from dbt_hakari.errors import UsageError
from dbt_hakari.graph import Node

PREFIXES = ("tag", "path", "uid", "name", "package")


def validate(patterns: list[str]) -> None:
    for pattern in patterns:
        prefix, sep, _ = pattern.partition(":")
        if sep and prefix not in PREFIXES:
            raise UsageError(
                f"unknown selector {pattern!r}: use tag:, path:, uid:, name:, package: or a "
                "model name glob"
            )


def matches(node: Node, pattern: str) -> bool:
    kind, _, value = pattern.partition(":")
    if value == "":
        return fnmatchcase(node.name, pattern)
    if kind == "tag":
        return value in node.tags
    if kind == "path":
        return node.path is not None and fnmatchcase(node.path, value)
    if kind == "uid":
        return fnmatchcase(node.uid, value)
    if kind == "name":
        return fnmatchcase(node.name, value)
    if kind == "package":
        return node.package == value
    return fnmatchcase(node.name, pattern)


def matching_pattern(node: Node, patterns: list[str]) -> str | None:
    for pattern in patterns:
        if matches(node, pattern):
            return pattern
    return None
