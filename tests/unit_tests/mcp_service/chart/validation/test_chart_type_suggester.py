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

"""Unit tests for the MCP chart type suggester."""

from typing import Any
from unittest.mock import patch

import pytest

from superset.mcp_service.chart.schemas import (
    ColumnRef,
    PieChartConfig,
    TableChartConfig,
    XYChartConfig,
)
from superset.mcp_service.chart.validation.runtime.chart_type_suggester import (
    ChartTypeSuggester,
)


def xy_config(**kwargs: Any) -> XYChartConfig:
    defaults: dict[str, Any] = {
        "x": ColumnRef(name="ds"),
        "y": [ColumnRef(name="num", aggregate="SUM")],
    }
    return XYChartConfig(**{**defaults, **kwargs})


def test_analyze_and_suggest_returns_true_for_unhandled_chart_type() -> None:
    config = PieChartConfig(
        dimension=ColumnRef(name="category"),
        metric=ColumnRef(name="num", aggregate="SUM"),
    )

    assert ChartTypeSuggester.analyze_and_suggest(config, 1) == (True, None)


def test_analyze_and_suggest_swallows_analysis_errors() -> None:
    config = xy_config()

    with patch.object(
        ChartTypeSuggester, "_analyze_xy_chart", side_effect=ValueError("boom")
    ):
        assert ChartTypeSuggester.analyze_and_suggest(config, 1) == (True, None)


def test_analyze_and_suggest_skips_xy_chart_without_x_axis() -> None:
    config = xy_config(x=None)

    assert ChartTypeSuggester.analyze_and_suggest(config, "1") == (True, None)


def test_temporal_x_axis_line_chart_is_appropriate() -> None:
    config = xy_config(x=ColumnRef(name="order_date"), kind="line")

    assert ChartTypeSuggester.analyze_and_suggest(config, 1) == (True, None)


def test_line_chart_with_categorical_x_axis_is_flagged() -> None:
    config = xy_config(x=ColumnRef(name="region"), kind="line")

    is_appropriate, info = ChartTypeSuggester.analyze_and_suggest(config, 1)

    assert is_appropriate is False
    assert info is not None
    assert "may not show meaningful trends" in info["issues"][0]
    assert (
        "Consider using a bar chart for categorical comparisons" in info["suggestions"]
    )
    assert info["recommended_types"] == ["bar", "table"]


def test_line_chart_with_id_x_axis_is_flagged() -> None:
    config = xy_config(x=ColumnRef(name="user_id"), kind="line")

    is_appropriate, info = ChartTypeSuggester.analyze_and_suggest(config, 1)

    assert is_appropriate is False
    assert info is not None
    assert "will not show meaningful patterns" in info["issues"][0]
    assert "Use a table to display individual records" in info["suggestions"]
    # X axis is neither temporal nor categorical
    assert info["recommended_types"] == ["scatter", "line", "table"]


def test_scatter_chart_with_categorical_x_and_multiple_metrics_is_flagged() -> None:
    config = xy_config(
        x=ColumnRef(name="country"),
        y=[
            ColumnRef(name="num", aggregate="SUM"),
            ColumnRef(name="num", aggregate="AVG"),
        ],
        kind="scatter",
    )

    is_appropriate, info = ChartTypeSuggester.analyze_and_suggest(config, 1)

    assert is_appropriate is False
    assert info is not None
    assert len(info["issues"]) == 2
    assert "may not effectively show correlations" in info["issues"][0]
    assert info["issues"][1] == (
        "Scatter plots with multiple Y metrics can be confusing"
    )


def test_area_chart_with_non_temporal_x_axis_is_flagged() -> None:
    config = xy_config(x=ColumnRef(name="product"), kind="area")

    is_appropriate, info = ChartTypeSuggester.analyze_and_suggest(config, 1)

    assert is_appropriate is False
    assert info is not None
    assert "may be misleading" in info["issues"][0]


def test_area_chart_with_negative_prone_metric_is_flagged() -> None:
    config = xy_config(
        x=ColumnRef(name="order_date"),
        y=[ColumnRef(name="net_loss", aggregate="SUM")],
        kind="area",
    )

    is_appropriate, info = ChartTypeSuggester.analyze_and_suggest(config, 1)

    assert is_appropriate is False
    assert info is not None
    assert "can create visual confusion" in info["issues"][0]
    assert "Use a line chart for data that can go negative" in info["suggestions"]
    assert info["recommended_types"] == ["line", "area", "bar", "scatter", "table"]


def test_area_chart_ignores_metrics_without_a_name() -> None:
    config = xy_config(
        x=ColumnRef(name="product"),
        y=[ColumnRef(sql_expression="COUNT(*)", label="records")],
        kind="area",
    )

    _, info = ChartTypeSuggester.analyze_and_suggest(config, 1)

    assert info is not None
    assert len(info["issues"]) == 1


def test_bar_chart_with_id_x_axis_is_flagged() -> None:
    config = xy_config(x=ColumnRef(name="order_uuid"), kind="bar")

    is_appropriate, info = ChartTypeSuggester.analyze_and_suggest(config, 1)

    assert is_appropriate is False
    assert info is not None
    assert "may create too many bars" in info["issues"][0]


def test_bar_chart_with_categorical_x_axis_is_appropriate() -> None:
    config = xy_config(x=ColumnRef(name="department"), kind="bar")

    assert ChartTypeSuggester.analyze_and_suggest(config, 1) == (True, None)


@pytest.mark.parametrize(
    "column,expected",
    [
        (ColumnRef(name="num", aggregate="COUNT"), True),
        (ColumnRef(name="num", aggregate="COUNT_DISTINCT"), True),
        (ColumnRef(sql_expression="count(*)", label="rows"), True),
        (ColumnRef(name="num", aggregate="SUM"), False),
        (ColumnRef(sql_expression="SUM(num)", label="total"), False),
    ],
)
def test_analyze_y_axis_detects_counts(column: ColumnRef, expected: bool) -> None:
    analysis = ChartTypeSuggester._analyze_y_axis([column])

    assert analysis == {"has_count": expected, "num_metrics": 1}


def test_count_by_category_gets_frequency_suggestion() -> None:
    config = xy_config(
        x=ColumnRef(name="status"),
        y=[ColumnRef(name="id", aggregate="COUNT")],
        kind="line",
    )

    _, info = ChartTypeSuggester.analyze_and_suggest(config, 1)

    assert info is not None
    assert any("frequency analysis" in s for s in info["suggestions"])
    assert info["recommended_types"] == ["bar", "table", "pie"]


def test_many_metrics_over_time_gets_focus_suggestion() -> None:
    config = xy_config(
        x=ColumnRef(name="created_at"),
        y=[
            ColumnRef(name="a", aggregate="SUM"),
            ColumnRef(name="b", aggregate="SUM"),
            ColumnRef(name="c", aggregate="SUM"),
            ColumnRef(name="d", aggregate="SUM"),
        ],
        kind="area",
    )
    # temporal x-axis is fine for area charts, so force an issue via an ID metric
    config.y[0].name = "loss"

    _, info = ChartTypeSuggester.analyze_and_suggest(config, 1)

    assert info is not None
    assert any("consider focusing on 2-3 key metrics" in s for s in info["suggestions"])


def test_single_metric_over_time_gets_trend_suggestion() -> None:
    x_analysis = ChartTypeSuggester._analyze_x_axis("event_time")
    y_analysis = {"has_count": False, "num_metrics": 1}

    suggestions = ChartTypeSuggester._get_general_suggestions(x_analysis, y_analysis)

    assert suggestions == [
        "Single metric over time - line charts are ideal for showing trends"
    ]


def test_table_chart_with_few_dimensions_is_flagged() -> None:
    config = TableChartConfig(
        columns=[
            ColumnRef(name="region"),
            ColumnRef(name="num", aggregate="SUM"),
        ]
    )

    is_appropriate, info = ChartTypeSuggester.analyze_and_suggest(config, 1)

    assert is_appropriate is False
    assert info is not None
    assert info["issues"] == [
        "Table with mostly aggregated metrics could be visualized as a chart"
    ]
    assert info["recommended_types"] == ["table", "pivot_table"]


def test_wide_table_is_flagged_and_recommends_table_only() -> None:
    config = TableChartConfig(columns=[ColumnRef(name=f"col_{i}") for i in range(11)])

    is_appropriate, info = ChartTypeSuggester.analyze_and_suggest(config, 1)

    assert is_appropriate is False
    assert info is not None
    assert "Table with 11 columns may be difficult to read" in info["issues"]
    assert info["recommended_types"] == ["table"]


def test_id_heavy_table_gets_inspection_suggestion() -> None:
    config = TableChartConfig(
        columns=[ColumnRef(name=f"col_{i}_id") for i in range(11)]
    )

    _, info = ChartTypeSuggester.analyze_and_suggest(config, 1)

    assert info is not None
    assert any("ID-heavy" in s for s in info["suggestions"])


def test_table_chart_with_raw_columns_only_is_appropriate() -> None:
    config = TableChartConfig(
        columns=[
            ColumnRef(name="region"),
            ColumnRef(name="product"),
            ColumnRef(name="channel"),
        ]
    )

    assert ChartTypeSuggester.analyze_and_suggest(config, 1) == (True, None)


@pytest.mark.parametrize(
    "chart_type,expected_prefix",
    [
        ("line", "Best for showing trends"),
        ("pie", "Shows proportions of a whole"),
        ("sunburst", "Visualizes data using sunburst format"),
    ],
)
def test_get_chart_type_description(chart_type: str, expected_prefix: str) -> None:
    assert ChartTypeSuggester.get_chart_type_description(chart_type).startswith(
        expected_prefix
    )
