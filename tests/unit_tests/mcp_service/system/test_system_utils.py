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

"""Tests for system-level utility functions."""

import logging
from unittest.mock import MagicMock, patch

from superset.mcp_service.system.system_utils import (
    calculate_dashboard_breakdown,
    calculate_database_breakdown,
    calculate_feature_availability,
    calculate_instance_summary,
)


def test_calculate_feature_availability_returns_menus():
    """Test that accessible menus are returned."""
    mock_sm = MagicMock()
    mock_sm.user_view_menu_names.return_value = {
        "SQL Lab",
        "Dashboards",
        "Charts",
    }

    with patch("superset.security_manager", mock_sm):
        result = calculate_feature_availability({}, {}, {})

    assert result.accessible_menus == ["Charts", "Dashboards", "SQL Lab"]
    mock_sm.user_view_menu_names.assert_called_once_with("menu_access")


def test_calculate_feature_availability_empty_when_no_context():
    """Test graceful fallback when security manager is unavailable."""
    broken_sm = MagicMock()
    broken_sm.user_view_menu_names.side_effect = RuntimeError("no ctx")

    with patch("superset.security_manager", broken_sm):
        result = calculate_feature_availability({}, {}, {})

    assert result.accessible_menus == []


def test_calculate_feature_availability_menus_sorted():
    """Test that accessible menus are returned in sorted order."""
    mock_sm = MagicMock()
    mock_sm.user_view_menu_names.return_value = {"Zzz", "Aaa", "Mmm"}

    with patch("superset.security_manager", mock_sm):
        result = calculate_feature_availability({}, {}, {})

    assert result.accessible_menus == ["Aaa", "Mmm", "Zzz"]


def test_calculate_dashboard_breakdown_logs_and_falls_back_on_error(caplog):
    """A failure is logged (not silently swallowed) and zeros are returned."""
    broken_dao = MagicMock()
    broken_dao.count.side_effect = RuntimeError("boom")

    with caplog.at_level(logging.ERROR):
        result = calculate_dashboard_breakdown(
            base_counts={"total_dashboards": 5},
            time_metrics={},
            dao_classes={"dashboards": broken_dao},
        )

    assert result.published == 0
    assert result.certified == 0
    assert result.with_charts == 0
    assert "Failed to calculate dashboard breakdown" in caplog.text


def test_calculate_database_breakdown_logs_and_falls_back_on_error(caplog):
    """A failure is logged (not silently swallowed) and an empty map returned."""
    broken_db = MagicMock()
    broken_db.session.query.side_effect = RuntimeError("boom")

    with caplog.at_level(logging.ERROR):
        with patch("superset.extensions.db", broken_db):
            result = calculate_database_breakdown({}, {}, {})

    assert result.by_type == {}
    assert "Failed to calculate database breakdown" in caplog.text


def test_calculate_instance_summary_logs_and_falls_back_on_error(caplog):
    """A failure is logged (not silently swallowed) and zeros are returned."""
    broken_db = MagicMock()
    broken_db.session.query.side_effect = RuntimeError("boom")

    with caplog.at_level(logging.ERROR):
        with patch("superset.extensions.db", broken_db):
            result = calculate_instance_summary(
                base_counts={"total_dashboards": 3, "total_charts": 9},
                time_metrics={},
                dao_classes={},
            )

    assert result.total_dashboards == 0
    assert result.total_charts == 0
    assert result.avg_charts_per_dashboard == 0.0
    assert "Failed to calculate instance summary" in caplog.text
