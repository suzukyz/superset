# Licensed to the Apache Software Foundation (ASF) under one
# or more contributor license agreements.  See the NOTICE file
# distributed with this work for additional information
# regarding copyright ownership.  The ASF licenses this file
# to you under the Apache License, Version 2.0 (the
# "License"); you may not use this file except in compliance
# with the License.  You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing,
# software distributed under the License is distributed on an
# "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
# KIND, either express or implied.  See the License for the
# specific language governing permissions and limitations
# under the License.

"""Unit tests for the DB engine spec introspection helpers."""

from pathlib import Path
from typing import Any

import pytest
import yaml
from pytest_mock import MockerFixture

from superset.constants import TimeGrain
from superset.db_engine_specs.base import BaseEngineSpec, DatabaseCategory
from superset.db_engine_specs.lib import (
    calculate_support_level,
    diagnose,
    format_markdown_table,
    generate_feature_tables,
    generate_focused_table,
    generate_table,
    generate_yaml_docs,
    get_documentation_metadata,
    get_name,
    has_custom_method,
    infer_category,
)
from superset.db_engine_specs.postgres import PostgresBaseEngineSpec, PostgresEngineSpec
from superset.db_engine_specs.sqlite import SqliteEngineSpec


def test_has_custom_method_without_override() -> None:
    assert has_custom_method(SqliteEngineSpec, "mask_encrypted_extra") is False


def test_has_custom_method_with_override() -> None:
    class CustomSpec(BaseEngineSpec):
        @classmethod
        def mask_encrypted_extra(cls, encrypted_extra: str | None) -> str | None:
            return encrypted_extra

    assert has_custom_method(CustomSpec, "mask_encrypted_extra") is True


def test_has_custom_method_for_unknown_method() -> None:
    assert has_custom_method(SqliteEngineSpec, "not_a_method") is False


def test_diagnose_reports_capabilities_and_score() -> None:
    output = diagnose(SqliteEngineSpec)

    assert output["module"] == "superset.db_engine_specs.sqlite"
    assert output["joins"] is True
    assert output["sql_validation"] is False
    assert set(output["time_grains"]) == {grain.name for grain in TimeGrain}
    assert output["max_score"] == len(TimeGrain) + 10 * 18
    assert 0 < output["score"] <= output["max_score"]


def test_diagnose_detects_external_sql_validators() -> None:
    assert diagnose(PostgresEngineSpec)["sql_validation"] is True


def test_get_name_falls_back_to_engine() -> None:
    class NamelessSpec(BaseEngineSpec):
        engine = "nameless"
        engine_name = None

    assert get_name(SqliteEngineSpec) == "SQLite"
    assert get_name(NamelessSpec) == "nameless"


def test_format_markdown_table() -> None:
    table = format_markdown_table(["a", "b"], [[1, True], ["x", None]])

    assert table == ("| a | b |\n| --- | --- |\n| 1 | True |\n| x | None |")


def test_generate_focused_table_sorts_databases_by_name() -> None:
    info = {
        "Zeta": {"joins": True},
        "Alpha": {"joins": False},
    }

    table, excluded = generate_focused_table(info, ["joins"], ["JOINs"])

    assert excluded == []
    assert table.splitlines()[2:] == ["| Alpha | False |", "| Zeta | True |"]


def test_generate_focused_table_preserves_order_and_fills_missing_keys() -> None:
    info = {"Zeta": {"joins": True}, "Alpha": {}}

    table, _ = generate_focused_table(info, ["joins"], ["JOINs"], preserve_order=True)

    assert table.splitlines()[2:] == ["| Zeta | True |", "| Alpha |  |"]


def test_generate_focused_table_filters_and_extracts_values() -> None:
    info = {
        "Alpha": {"joins": True, "score": 10},
        "Beta": {"joins": False, "score": 5},
    }

    table, excluded = generate_focused_table(
        info,
        ["score"],
        ["Score"],
        filter_fn=lambda db_info: db_info["joins"],
        value_extractor=lambda db_info, key: db_info[key] * 2,
    )

    assert excluded == ["Beta"]
    assert table.splitlines()[2:] == ["| Alpha | 20 |"]


def test_generate_focused_table_with_everything_filtered_out() -> None:
    table, excluded = generate_focused_table(
        {"Alpha": {}}, ["joins"], ["JOINs"], filter_fn=lambda db_info: False
    )

    assert (table, excluded) == ("", ["Alpha"])


@pytest.mark.parametrize(
    "db_info,feature_keys,expected",
    [
        ({}, [], "Not supported"),
        ({"a": True, "b": True}, ["a", "b"], "Supported"),
        ({"a": True, "b": False}, ["a", "b"], "Partial"),
        ({"a": False}, ["a"], "Not supported"),
        (
            {"time_grains": {"DAY": True, "WEEK": False}},
            ["time_grains.DAY", "time_grains.WEEK"],
            "Partial",
        ),
        (
            {"time_grains": {"DAY": True}},
            ["time_grains.DAY"],
            "Supported",
        ),
    ],
)
def test_calculate_support_level(
    db_info: dict[str, Any], feature_keys: list[str], expected: str
) -> None:
    assert calculate_support_level(db_info, feature_keys) == expected


@pytest.mark.parametrize(
    "name,expected",
    [
        ("Amazon Athena", DatabaseCategory.CLOUD_AWS),
        ("Google BigQuery", DatabaseCategory.CLOUD_GCP),
        ("Microsoft SQL Server", DatabaseCategory.CLOUD_AZURE),
        ("Snowflake", DatabaseCategory.CLOUD_DATA_WAREHOUSES),
        ("Apache Druid", DatabaseCategory.APACHE_PROJECTS),
        ("PostgreSQL", DatabaseCategory.TRADITIONAL_RDBMS),
        ("ClickHouse", DatabaseCategory.ANALYTICAL_DATABASES),
        ("Elasticsearch", DatabaseCategory.SEARCH_NOSQL),
        ("Trino", DatabaseCategory.QUERY_ENGINES),
        ("Something Else", DatabaseCategory.OTHER),
    ],
)
def test_infer_category(name: str, expected: str) -> None:
    assert infer_category(name) == expected


def test_get_documentation_metadata_adds_missing_category() -> None:
    class SpecWithMetadata(BaseEngineSpec):
        metadata = {"description": "A database"}

    metadata = get_documentation_metadata(SpecWithMetadata, "Trino")

    assert metadata == {
        "description": "A database",
        "category": DatabaseCategory.QUERY_ENGINES,
    }


def test_get_documentation_metadata_keeps_declared_category() -> None:
    class SpecWithCategory(BaseEngineSpec):
        metadata = {  # type: ignore[typeddict-unknown-key]
            "category": DatabaseCategory.OTHER
        }

    metadata = get_documentation_metadata(SpecWithCategory, "Trino")

    assert metadata["category"] == DatabaseCategory.OTHER


def test_get_documentation_metadata_without_metadata() -> None:
    class SpecWithoutMetadata(BaseEngineSpec):
        sqlalchemy_uri_placeholder = "mydb://user:password@host/db"

    metadata = get_documentation_metadata(SpecWithoutMetadata, "MyDB")

    assert metadata == {
        "pypi_packages": [],
        "connection_string": "mydb://user:password@host/db",
        "category": DatabaseCategory.OTHER,
    }


def test_generate_table_lists_features_per_database(mocker: MockerFixture) -> None:
    mocker.patch(
        "superset.db_engine_specs.lib.load_engine_specs",
        return_value=[SqliteEngineSpec, PostgresEngineSpec],
    )

    rows = generate_table()

    assert rows[0] == ["Feature", "PostgreSQL", "SQLite"]
    assert rows[1] == [
        "Module",
        "superset.db_engine_specs.postgres",
        "superset.db_engine_specs.sqlite",
    ]
    assert rows[2][0] == "Method used to limit the rows in the subquery"
    assert rows[-1][0] == "Score"
    assert all(isinstance(score, int) for score in rows[-1][1:])


def test_generate_table_excludes_third_party_specs(mocker: MockerFixture) -> None:
    class ThirdPartySpec(BaseEngineSpec):
        engine = "thirdparty"
        engine_name = "Third Party"

    ThirdPartySpec.__module__ = "acme.db_engine_specs"
    mocker.patch(
        "superset.db_engine_specs.lib.load_engine_specs",
        return_value=[SqliteEngineSpec, ThirdPartySpec],
    )

    assert generate_table()[0] == ["Feature", "SQLite"]


def test_generate_feature_tables(mocker: MockerFixture) -> None:
    mocker.patch(
        "superset.db_engine_specs.lib.load_engine_specs",
        return_value=[SqliteEngineSpec, PostgresEngineSpec],
    )

    output = generate_feature_tables()

    for section in (
        "### Feature Overview",
        "### Database Information",
        "### SQL Capabilities",
        "### Time Grains – Common",
        "### Time Grains – Extended",
        "### Core Platform & Metadata Features",
        "### Operational & Advanced Features",
    ):
        assert section in output
    assert "| PostgreSQL |" in output
    assert "FORCE_LIMIT" in output


def test_generate_yaml_docs_returns_documentation(mocker: MockerFixture) -> None:
    mocker.patch(
        "superset.db_engine_specs.lib.load_engine_specs",
        return_value=[SqliteEngineSpec],
    )

    docs = generate_yaml_docs()

    assert list(docs) == ["SQLite"]
    assert docs["SQLite"]["engine"] == "sqlite"
    assert docs["SQLite"]["engine_name"] == "SQLite"
    assert docs["SQLite"]["supports_file_upload"] is True
    assert "documentation" in docs["SQLite"]


def test_generate_yaml_docs_skips_base_and_third_party_specs(
    mocker: MockerFixture,
) -> None:
    class ThirdPartySpec(BaseEngineSpec):
        engine = "thirdparty"
        engine_name = "Third Party"

    ThirdPartySpec.__module__ = "acme.db_engine_specs"
    mocker.patch(
        "superset.db_engine_specs.lib.load_engine_specs",
        return_value=[PostgresEngineSpec, PostgresBaseEngineSpec, ThirdPartySpec],
    )

    docs = generate_yaml_docs()

    assert list(docs) == ["PostgreSQL"]
    assert docs["PostgreSQL"]["engine"] == "postgresql"


def test_generate_yaml_docs_writes_files(mocker: MockerFixture, tmp_path: Path) -> None:
    mocker.patch(
        "superset.db_engine_specs.lib.load_engine_specs",
        return_value=[SqliteEngineSpec],
    )

    output_dir = tmp_path / "databases"
    generate_yaml_docs(str(output_dir))

    with open(output_dir / "sqlite.yaml") as file:
        contents = yaml.safe_load(file)
    with open(output_dir / "_index.yaml") as file:
        index = yaml.safe_load(file)

    assert contents["SQLite"]["engine"] == "sqlite"
    assert list(index) == ["SQLite"]
