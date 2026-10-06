"""BigQuery backend. Strictly read-only: dry-runs, plus one fixed SELECT on INFORMATION_SCHEMA."""

from __future__ import annotations

import re
from collections.abc import Iterable

from dbt_hakari.backends.base import DryRunResult, JobRecord
from dbt_hakari.errors import BackendError

_JOBS_SQL = r"""
WITH jobs AS (
  SELECT
    (query LIKE '%"app": "dbt"%'
     AND REGEXP_CONTAINS(query, r'"node_id": "(model|test|seed|snapshot)\.')) AS from_dbt,
    query, total_bytes_billed, total_bytes_processed, cache_hit, error_result, statement_type,
    reservation_id, creation_time, referenced_tables, user_email
  FROM `region-{location}`.INFORMATION_SCHEMA.JOBS_BY_PROJECT
  WHERE creation_time >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL {days} DAY)
    AND job_type = 'QUERY'
    AND state = 'DONE'
)
SELECT
  IF(from_dbt, REGEXP_EXTRACT(query, r'"node_id": "([^"]+)"'), NULL) AS node_id,
  from_dbt,
  total_bytes_billed AS billed_bytes,
  total_bytes_processed AS processed_bytes,
  user_email AS user,
  IFNULL(cache_hit, FALSE) AS cache_hit,
  error_result IS NOT NULL AS error,
  statement_type,
  reservation_id,
  FORMAT_DATE('%Y-%m-%d', DATE(creation_time)) AS creation_day,
  IF(from_dbt, [], ARRAY(SELECT CONCAT(t.project_id, '.', t.dataset_id, '.', t.table_id)
                         FROM UNNEST(referenced_tables) AS t)) AS referenced
FROM jobs
WHERE from_dbt OR ARRAY_LENGTH(referenced_tables) > 0
"""

_LOCATION = re.compile(r"^[A-Za-z0-9-]+$")  # US, EU, europe-west1, ...


class BigQueryBackend:
    def __init__(
        self,
        project: str | None,
        location: str,
        max_bytes_billed: int = 1024**3,
    ) -> None:
        if not _LOCATION.match(location):
            raise BackendError(f"invalid location: {location!r}")
        try:
            from google.cloud import bigquery
        except ImportError as error:  # pragma: no cover
            raise BackendError("google-cloud-bigquery is not installed") from error
        self._bigquery = bigquery
        self._location = location
        self._max_bytes_billed = max_bytes_billed
        try:
            self._client = bigquery.Client(project=project, location=location)
        except Exception as error:  # DefaultCredentialsError and friends
            raise BackendError(
                f"cannot connect to BigQuery: {error}. Log in with "
                "`gcloud auth application-default login` and pass --project"
            ) from error
        self.project = self._client.project

    def dry_run(self, sql: str) -> DryRunResult:
        config = self._bigquery.QueryJobConfig(dry_run=True, use_query_cache=False)
        try:
            job = self._client.query(sql, job_config=config)
        except Exception as error:
            raise BackendError(str(error)) from error
        tables = frozenset(
            f"{t.project}.{t.dataset_id}.{t.table_id}" for t in (job.referenced_tables or [])
        )
        return DryRunResult(int(job.total_bytes_processed or 0), tables)

    def list_jobs(self, lookback_days: int) -> Iterable[JobRecord]:
        sql = _JOBS_SQL.format(location=self._location.lower(), days=int(lookback_days))
        config = self._bigquery.QueryJobConfig(maximum_bytes_billed=self._max_bytes_billed)
        try:
            rows = list(self._client.query(sql, job_config=config).result())
        except Exception as error:
            raise BackendError(f"cannot read job history: {error}") from error
        for row in rows:
            yield JobRecord(
                node_id=row["node_id"],
                billed_bytes=int(row["billed_bytes"] or 0),
                cache_hit=bool(row["cache_hit"]),
                error=bool(row["error"]),
                statement_type=row["statement_type"] or "",
                reservation_id=row["reservation_id"],
                creation_day=row["creation_day"],
                processed_bytes=int(row["processed_bytes"] or 0),
                from_dbt=bool(row["from_dbt"]),
                referenced=tuple(row["referenced"] or ()),
                user=row["user"],
            )
