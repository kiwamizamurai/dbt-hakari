from __future__ import annotations

from dbt_hakari.cost import BigQueryOnDemand

MIB = 1024**2


def test_small_tables_are_billed_at_the_minimum_per_table():
    model = BigQueryOnDemand()
    assert model.billed_bytes(processed=187 * MIB, n_tables=60) == 600 * MIB


def test_large_scans_are_billed_by_bytes():
    model = BigQueryOnDemand()
    assert model.billed_bytes(processed=2300 * MIB, n_tables=3) == 2300 * MIB


def test_minimum_per_query_applies():
    model = BigQueryOnDemand()
    assert model.billed_bytes(processed=1, n_tables=0) == 10 * MIB


def test_price():
    model = BigQueryOnDemand(price_per_tib=6.0)
    assert model.price(1024**4) == 6.0
