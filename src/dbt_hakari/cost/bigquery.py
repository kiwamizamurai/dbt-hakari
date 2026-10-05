from __future__ import annotations

from dataclasses import dataclass

MIB = 1024**2
TIB = 1024**4


@dataclass(frozen=True)
class BigQueryOnDemand:
    """BigQuery on-demand pricing.

    The official documentation states that the minimum "processed data per referenced table" is
    10 MiB regardless of the table's actual size, and that the minimum per query is 10 MiB.
    Views are expanded, so every base table beneath a view counts.

    The model bills ``max(total bytes, 10 MiB x tables, 10 MiB)``. Applied literally, the
    per-table rule gives ``sum over tables of max(bytes of that table, 10 MiB)``. The two agree
    when every table is small (both give 10 MiB x tables) or every table is large (both give the
    total). They differ when a query reads one large and several small tables, where the model
    is optimistic by up to 10 MiB per small table. Per-table bytes are not available from a
    dry-run, so the model keeps the total; see docs/formulation.md.
    """

    name: str = "bigquery-on-demand"
    price_per_tib: float = 6.25
    currency: str = "USD"
    min_per_table: int = 10 * MIB
    min_per_query: int = 10 * MIB
    free_tib_per_month: float = 1.0

    def billed_bytes(self, processed: int, n_tables: int) -> int:
        return max(processed, self.min_per_table * n_tables, self.min_per_query)

    def min_unit_bytes(self) -> int:
        return self.min_per_table

    def price(self, billed_bytes: float) -> float:
        return billed_bytes / TIB * self.price_per_tib

    def describe_assumptions(self) -> list[str]:
        return [
            "billed = max(bytes processed, 10 MiB x referenced tables, 10 MiB); "
            "views are expanded to their base tables",
            "materializing a view does not change the bytes processed by its readers "
            "(conservative; the reader may in fact read fewer bytes)",
            "storage cost and data freshness are not modeled",
            f"price {self.price_per_tib} {self.currency}/TiB, first {self.free_tib_per_month} TiB "
            "per month free (not applied per node)",
        ]
