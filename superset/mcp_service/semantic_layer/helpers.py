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

"""Shared helpers for the semantic layer MCP tools."""

from __future__ import annotations

from sqlalchemy.orm import subqueryload

from superset.connectors.sqla.models import SqlaTable
from superset.daos.dataset import DatasetDAO
from superset.daos.semantic_layer import SemanticViewDAO
from superset.exceptions import SupersetSecurityException
from superset.extensions import event_logger
from superset.mcp_service.privacy import (
    DATA_MODEL_METADATA_ERROR_TYPE,
    user_can_view_data_model_metadata,
)
from superset.mcp_service.semantic_layer.schemas import (
    DimensionInfo,
    SemanticLayerError,
)
from superset.mcp_service.utils.query_utils import validate_names
from superset.semantic_layers.models import SemanticView


def validate_target(
    dataset_id: int | None,
    view_id: int | None,
) -> SemanticLayerError | None:
    """
    Check the caller can read metadata and targeted exactly one source.

    :returns: the error to return to the caller, or None when valid
    """
    if not user_can_view_data_model_metadata():
        return SemanticLayerError.create(
            error="You don't have permission to access dataset details for your role.",
            error_type=DATA_MODEL_METADATA_ERROR_TYPE,
        )

    if dataset_id is None and view_id is None:
        return SemanticLayerError.create(
            error="Provide either dataset_id (built-in) or view_id (external).",
            error_type="ValidationError",
        )
    if dataset_id is not None and view_id is not None:
        return SemanticLayerError.create(
            error="Provide only one of dataset_id or view_id, not both.",
            error_type="ValidationError",
        )

    return None


def load_dataset(dataset_id: int, action: str) -> SqlaTable | SemanticLayerError:
    """
    Load a built-in dataset with its columns and metrics eagerly loaded.

    :param action: event logger action name for the query
    """
    with event_logger.log_context(action=action):
        dataset: SqlaTable | None = DatasetDAO.find_by_id(
            dataset_id,
            query_options=[
                subqueryload(SqlaTable.columns),
                subqueryload(SqlaTable.metrics),
            ],
        )

    if dataset is None:
        return SemanticLayerError.create(
            error=f"No dataset found with id: {dataset_id}.",
            error_type="NotFound",
        )

    return dataset


def validate_selection(
    dataset: SqlaTable,
    selected_metrics: list[str],
    selected_dimensions: list[str],
) -> SemanticLayerError | None:
    """
    Reject selections referencing metrics or columns the dataset doesn't have.
    """
    valid_metrics = {metric.metric_name for metric in dataset.metrics}
    valid_columns = {column.column_name for column in dataset.columns}
    validation_errors = validate_names(
        selected_metrics,
        valid_metrics,
        "metric",
        list_valid_on_miss=True,
        full_list_hint="call list_metrics for the full list",
    )
    validation_errors.extend(
        validate_names(selected_dimensions, valid_columns, "dimension")
    )
    if validation_errors:
        return SemanticLayerError.create(
            error="; ".join(validation_errors),
            error_type="ValidationError",
        )

    return None


def load_semantic_view(view_id: int, action: str) -> SemanticView | SemanticLayerError:
    """
    Load an external semantic view the current user is allowed to access.

    :param action: event logger action name for the query
    """
    with event_logger.log_context(action=action):
        view: SemanticView | None = SemanticViewDAO.find_by_id(view_id)

    if view is None:
        return SemanticLayerError.create(
            error=f"No semantic view found with id: {view_id}.",
            error_type="NotFound",
        )

    try:
        view.raise_for_access()
    except SupersetSecurityException as ex:
        return SemanticLayerError.create(
            error=str(ex.error.message),
            error_type="AccessDenied",
        )

    return view


def builtin_dimensions(dataset: SqlaTable) -> list[DimensionInfo]:
    """Return the groupby-enabled columns of a built-in dataset as dimensions."""
    return [
        DimensionInfo(
            name=col.column_name,
            verbose_name=col.verbose_name or None,
            description=col.description or None,
            type=col.type or None,
            is_dttm=bool(col.is_dttm),
            groupby=bool(col.groupby),
            filterable=bool(col.filterable),
            source="builtin",
        )
        for col in dataset.columns
        if col.groupby
    ]
