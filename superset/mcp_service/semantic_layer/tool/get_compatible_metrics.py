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

"""MCP tool: get_compatible_metrics

Returns metrics compatible with the current dimension/metric selection.
"""

import logging

from fastmcp import Context
from superset_core.mcp.decorators import tool, ToolAnnotations

from superset.mcp_service.privacy import requires_data_model_metadata_access
from superset.mcp_service.semantic_layer.helpers import (
    load_dataset,
    load_semantic_view,
    validate_selection,
    validate_target,
)
from superset.mcp_service.semantic_layer.schemas import (
    CompatibleMetricsResponse,
    GetCompatibleMetricsRequest,
    MetricInfo,
    SemanticLayerError,
)

logger = logging.getLogger(__name__)


@tool(
    tags=["data", "semantic"],
    class_permission_name="Dataset",
    annotations=ToolAnnotations(
        title="Get compatible metrics",
        readOnlyHint=True,
        destructiveHint=False,
    ),
)
@requires_data_model_metadata_access
async def get_compatible_metrics(
    request: GetCompatibleMetricsRequest,
    ctx: Context,
) -> CompatibleMetricsResponse | SemanticLayerError:
    """Return metrics compatible with the current dimension/metric selection.

    Used to progressively refine a query: given a set of already-selected
    metrics and dimensions, returns the additional metrics that can be
    combined without breaking the underlying semantic constraints.

    Provide exactly one of ``dataset_id`` (built-in) or ``view_id`` (external).

    For built-in datasets, all metrics from the dataset are considered
    compatible (SQL GROUP BY imposes no metric-level constraints). Unknown
    names in ``selected_metrics`` or ``selected_dimensions`` are rejected
    with a ValidationError.

    For external semantic views, delegates to the view's
    ``get_compatible_metrics`` implementation.

    Example:
    ```json
    {
        "selected_metrics": [],
        "selected_dimensions": ["region"],
        "view_id": 5
    }
    ```
    """
    await ctx.info(
        "Getting compatible metrics: dataset_id=%s, view_id=%s, "
        "metrics=%s, dims=%s"
        % (
            request.dataset_id,
            request.view_id,
            request.selected_metrics,
            request.selected_dimensions,
        )
    )

    if target_error := validate_target(request.dataset_id, request.view_id):
        return target_error

    try:
        # ------------------------------------------------------------------
        # Built-in dataset path
        # ------------------------------------------------------------------
        if request.dataset_id is not None:
            dataset = load_dataset(
                request.dataset_id, "mcp.get_compatible_metrics.builtin"
            )
            if isinstance(dataset, SemanticLayerError):
                return dataset

            if selection_error := validate_selection(
                dataset, request.selected_metrics, request.selected_dimensions
            ):
                return selection_error

            # All metrics on a SQL dataset are always mutually compatible;
            # exclude ones already selected so clients don't get duplicate
            # suggestions for metrics they've already added.
            selected_metrics: set[str] = set(request.selected_metrics)
            compatible: list[MetricInfo] = [
                MetricInfo(
                    name=m.metric_name,
                    verbose_name=m.verbose_name or None,
                    description=m.description or None,
                    expression=m.expression or None,
                    d3format=m.d3format or None,
                    warning_text=m.warning_text or None,
                    source="builtin",
                    dataset_id=dataset.id,
                    dataset_name=dataset.table_name,
                )
                for m in dataset.metrics
                if m.metric_name not in selected_metrics
            ]

            await ctx.info("Compatible metrics (builtin): count=%d" % len(compatible))
            return CompatibleMetricsResponse(
                compatible_metrics=compatible,
                source="builtin",
            )

        # ------------------------------------------------------------------
        # External semantic view path
        # ------------------------------------------------------------------
        from superset.semantic_layers.models import MetricMetadata

        view_id: int = request.view_id  # type: ignore[assignment]
        view = load_semantic_view(view_id, "mcp.get_compatible_metrics.external")
        if isinstance(view, SemanticLayerError):
            return view

        compatible_names: list[str] = view.get_compatible_metrics(
            request.selected_metrics,
            request.selected_dimensions,
        )

        # Enrich with full metric metadata
        all_metrics_map: dict[str, MetricMetadata] = {
            m.metric_name: m for m in view.metrics
        }
        compatible = [
            MetricInfo(
                name=name,
                description=(
                    all_metrics_map[name].description
                    if name in all_metrics_map
                    else None
                ),
                expression=(
                    all_metrics_map[name].expression
                    if name in all_metrics_map
                    else None
                ),
                source="external",
                view_id=view.id,
                view_name=view.name,
            )
            for name in compatible_names
        ]

        await ctx.info(
            "Compatible metrics (external view id=%d): count=%d"
            % (view.id, len(compatible))
        )
        return CompatibleMetricsResponse(
            compatible_metrics=compatible,
            source="external",
        )

    except Exception as exc:
        logger.exception(
            "Unexpected error in get_compatible_metrics: %s: %s",
            type(exc).__name__,
            str(exc),
        )
        await ctx.error("Unexpected error: %s: %s" % (type(exc).__name__, str(exc)))
        return SemanticLayerError.create(
            error=f"Internal error in get_compatible_metrics: {exc}",
            error_type="InternalError",
        )
