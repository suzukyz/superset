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

"""Unit tests for the DB engine spec metadata linter."""

import ast
import json  # noqa: TID251
import urllib.error
from pathlib import Path
from typing import Any

import pytest
from pytest_mock import MockerFixture

from superset.db_engine_specs import lint_metadata
from superset.db_engine_specs.lint_metadata import (
    _eval_ast_dict,
    _eval_ast_value,
    analyze_spec,
    check_pypi_package,
    generate_markdown_report,
    get_all_engine_specs,
    get_all_engine_specs_ast,
    main,
    MetadataReport,
    print_report,
    validate_pypi_packages,
)

COMPLETE_METADATA = {
    "description": "A database",
    "categories": ["TRADITIONAL_RDBMS"],
    "pypi_packages": ["mydriver"],
    "connection_string": "mydb://host",
    "logo": "mydb.svg",
    "homepage_url": "https://mydb.example.com/",
    "default_port": 5432,
}


@pytest.fixture(autouse=True)
def clear_pypi_cache() -> None:
    lint_metadata._pypi_cache.clear()


def test_check_pypi_package_strips_specifiers_and_caches(
    mocker: MockerFixture,
) -> None:
    urlopen = mocker.patch("urllib.request.urlopen")
    urlopen.return_value.__enter__.return_value.status = 200

    assert check_pypi_package("mydriver[extra]>=1.0") is True
    assert check_pypi_package("mydriver") is True

    urlopen.assert_called_once()
    assert urlopen.call_args[0][0].full_url == "https://pypi.org/pypi/mydriver/json"


def test_check_pypi_package_handles_network_errors(mocker: MockerFixture) -> None:
    mocker.patch(
        "urllib.request.urlopen", side_effect=urllib.error.URLError("no network")
    )

    assert check_pypi_package("missing-driver") is False


def test_validate_pypi_packages_splits_valid_and_invalid(
    mocker: MockerFixture,
) -> None:
    mocker.patch(
        "superset.db_engine_specs.lint_metadata.check_pypi_package",
        side_effect=lambda package, timeout: package == "good",
    )

    assert validate_pypi_packages(["good", "bad"]) == (["good"], ["bad"])


def test_analyze_spec_with_complete_metadata() -> None:
    report = analyze_spec(
        {
            "engine_name": "MyDB",
            "module": "mydb",
            "metadata": COMPLETE_METADATA,
        }
    )

    assert report.has_metadata is True
    assert report.missing_required == set()
    assert report.missing_recommended == set()
    assert report.completeness_score == 90.0


def test_analyze_spec_without_metadata_falls_back_to_class_name() -> None:
    report = analyze_spec({"class_name": "MyDBEngineSpec", "metadata": {}})

    assert report.engine_name == "MyDBEngineSpec"
    assert report.module == "unknown"
    assert report.has_metadata is False
    assert report.completeness_score == 0.0
    assert "description" in report.missing_required


def test_analyze_spec_treats_unparseable_metadata_as_missing() -> None:
    report = analyze_spec({"engine_name": "MyDB", "metadata": {"_unparseable": True}})

    assert report.has_metadata is False
    assert report.present_fields == set()


def test_analyze_spec_validates_pypi_packages(mocker: MockerFixture) -> None:
    mocker.patch(
        "superset.db_engine_specs.lint_metadata.validate_pypi_packages",
        return_value=([], ["nonexistent"]),
    )

    report = analyze_spec(
        {
            "engine_name": "MyDB",
            "metadata": {**COMPLETE_METADATA, "pypi_packages": ["nonexistent"]},
        },
        check_pypi=True,
    )

    assert report.invalid_packages == ["nonexistent"]


def test_metadata_report_to_dict_omits_empty_optional_fields() -> None:
    report = analyze_spec({"engine_name": "MyDB", "metadata": COMPLETE_METADATA})

    as_dict = report.to_dict()

    assert as_dict["engine_name"] == "MyDB"
    assert as_dict["present_fields"] == sorted(COMPLETE_METADATA)
    assert "invalid_packages" not in as_dict
    assert "limitations" not in as_dict


def test_metadata_report_to_dict_includes_limitations_and_packages() -> None:
    report = analyze_spec(
        {
            "engine_name": "MyDB",
            "metadata": {**COMPLETE_METADATA, "limitations": ["No JOINs"]},
        }
    )
    report.invalid_packages = ["nonexistent"]

    as_dict = report.to_dict()

    assert as_dict["limitations"] == ["No JOINs"]
    assert as_dict["invalid_packages"] == ["nonexistent"]


@pytest.mark.parametrize(
    "source,expected",
    [
        ("1", 1),
        ("'text'", "text"),
        ("['a', 'b']", ["a", "b"]),
        ("('a', 1)", ("a", 1)),
        ("{'a': 1}", {"a": 1}),
        ("DatabaseCategory", "DatabaseCategory"),
        ("DatabaseCategory.TRADITIONAL_RDBMS", "DatabaseCategory.TRADITIONAL_RDBMS"),
        ("'a' 'b'", "ab"),
        ("'a' + 'b'", "ab"),
        ("1 + 2", None),
        ("f'{x}'", "<f-string>"),
        ("lambda: 1", None),
    ],
)
def test_eval_ast_value(source: str, expected: Any) -> None:
    node = ast.parse(source, mode="eval").body

    assert _eval_ast_value(node) == expected


def test_eval_ast_dict_skips_unpacked_entries() -> None:
    node = ast.parse("{'a': 1, **other}", mode="eval").body

    assert _eval_ast_dict(node) == {"a": 1}


def test_eval_ast_dict_with_non_dict_node() -> None:
    node = ast.parse("[1]", mode="eval").body

    assert _eval_ast_dict(node) == {}


def write_spec_module(directory: Path, filename: str, source: str) -> None:
    (directory / filename).write_text(source)


def test_get_all_engine_specs_ast(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(lint_metadata, "__file__", str(tmp_path / "lint_metadata.py"))
    write_spec_module(tmp_path, "README.md", "not python")
    write_spec_module(tmp_path, "lib.py", "raise SystemExit")
    write_spec_module(tmp_path, "broken.py", "class Foo(BaseEngineSpec):\n  bad syntax")
    write_spec_module(
        tmp_path,
        "mydb.py",
        (
            "class MyDBEngineSpec(BaseEngineSpec):\n"
            "    engine = 'mydb'\n"
            "    engine_name = 'MyDB'\n"
            "    metadata = {'description': 'A database'}\n"
            "\n"
            "class MyDBBaseEngineSpec(BaseEngineSpec):\n"
            "    engine = ''\n"
            "\n"
            "class ConcreteBaseEngineSpec(BaseEngineSpec):\n"
            "    engine = 'concrete'\n"
            "\n"
            "class SomethingMixin(BaseEngineSpec):\n"
            "    engine = 'mixin'\n"
            "\n"
            "class NotASpec:\n"
            "    engine = 'nope'\n"
            "\n"
            "class SpreadMetadataEngineSpec(BaseEngineSpec):\n"
            "    engine = 'spread'\n"
            "    metadata = {**base_metadata}\n"
        ),
    )

    specs = get_all_engine_specs_ast()

    assert [spec["class_name"] for spec in specs] == [
        "ConcreteBaseEngineSpec",
        "MyDBEngineSpec",
        "SpreadMetadataEngineSpec",
    ]
    assert specs[1] == {
        "class_name": "MyDBEngineSpec",
        "engine_name": "MyDB",
        "module": "mydb",
        "metadata": {"description": "A database"},
    }
    assert specs[2]["metadata"] == {}
    assert "Could not parse broken.py" in capsys.readouterr().err


def test_get_all_engine_specs_skips_base_classes(mocker: MockerFixture) -> None:
    from superset.db_engine_specs.sqlite import SqliteEngineSpec

    mocker.patch(
        "superset.db_engine_specs.load_engine_specs",
        return_value=[SqliteEngineSpec, type("BaseEngineSpec", (), {})],
    )

    assert get_all_engine_specs() == [SqliteEngineSpec]


def build_reports() -> list[MetadataReport]:
    return [
        analyze_spec({"engine_name": "Complete", "metadata": COMPLETE_METADATA}),
        analyze_spec(
            {
                "engine_name": "Partial",
                "module": "partial",
                "metadata": {"description": "Partial database"},
            }
        ),
        analyze_spec({"engine_name": "Bare", "module": "bare", "metadata": {}}),
    ]


def test_print_report(capsys: pytest.CaptureFixture[str]) -> None:
    reports = build_reports()
    reports[0].invalid_packages = ["nonexistent"]

    print_report(reports, verbose=True)

    output = capsys.readouterr().out
    assert "Total engine specs:     3" in output
    assert "COMPLETE (1 specs" in output
    assert "NEEDS WORK (2 specs)" in output
    assert "NO METADATA (1 specs)" in output
    assert "Bare (bare.py)" in output
    assert "INVALID PyPI PACKAGES (1 specs)" in output
    assert "missing required: categories, connection_string, pypi_packages" in output
    assert "Optional fields:" in output


def test_print_report_without_verbose_hides_optional_fields(
    capsys: pytest.CaptureFixture[str],
) -> None:
    print_report(build_reports())

    assert "Optional fields:" not in capsys.readouterr().out


def test_generate_markdown_report() -> None:
    report = generate_markdown_report(build_reports())

    assert "# Database Metadata Completeness Report" in report
    assert "- **Total engine specs:** 3" in report
    assert "| Bare | bare.py | 0% |" in report
    assert "- Complete (90%)" in report


def test_main_json_output(
    mocker: MockerFixture, capsys: pytest.CaptureFixture[str]
) -> None:
    mocker.patch(
        "superset.db_engine_specs.lint_metadata.get_all_engine_specs_ast",
        return_value=[{"engine_name": "MyDB", "metadata": COMPLETE_METADATA}],
    )
    mocker.patch("sys.argv", ["lint_metadata.py", "--json"])

    assert main() == 0

    output = json.loads(capsys.readouterr().out)
    assert output["summary"] == {
        "total": 1,
        "with_metadata": 1,
        "all_required": 1,
        "average_score": 90.0,
    }
    assert output["reports"][0]["engine_name"] == "MyDB"


def test_main_markdown_output_to_file(mocker: MockerFixture, tmp_path: Path) -> None:
    mocker.patch(
        "superset.db_engine_specs.lint_metadata.get_all_engine_specs_ast",
        return_value=[{"engine_name": "MyDB", "metadata": COMPLETE_METADATA}],
    )
    output_file = tmp_path / "report.md"
    mocker.patch(
        "sys.argv",
        ["lint_metadata.py", "--markdown", "--output", str(output_file)],
    )

    assert main() == 0
    assert "# Database Metadata Completeness Report" in output_file.read_text()


def test_main_without_specs(
    mocker: MockerFixture, capsys: pytest.CaptureFixture[str]
) -> None:
    mocker.patch(
        "superset.db_engine_specs.lint_metadata.get_all_engine_specs_ast",
        return_value=[],
    )
    mocker.patch("sys.argv", ["lint_metadata.py"])

    assert main() == 1
    assert "No engine specs found" in capsys.readouterr().err


def test_main_strict_fails_on_incomplete_metadata(
    mocker: MockerFixture, capsys: pytest.CaptureFixture[str]
) -> None:
    mocker.patch(
        "superset.db_engine_specs.lint_metadata.get_all_engine_specs_ast",
        return_value=[
            {"engine_name": "MyDB", "metadata": {"description": "incomplete"}},
            # specs without metadata are tolerated
            {"engine_name": "Legacy", "metadata": {}},
        ],
    )
    mocker.patch("sys.argv", ["lint_metadata.py", "--strict"])

    assert main() == 1
    assert "1 specs missing required fields" in capsys.readouterr().err


def test_main_strict_fails_on_invalid_pypi_packages(
    mocker: MockerFixture, capsys: pytest.CaptureFixture[str]
) -> None:
    mocker.patch(
        "superset.db_engine_specs.lint_metadata.get_all_engine_specs_ast",
        return_value=[{"engine_name": "MyDB", "metadata": COMPLETE_METADATA}],
    )
    mocker.patch(
        "superset.db_engine_specs.lint_metadata.validate_pypi_packages",
        return_value=([], ["mydriver"]),
    )
    mocker.patch("sys.argv", ["lint_metadata.py", "--strict", "--check-pypi"])

    assert main() == 1
    assert "1 specs have invalid packages" in capsys.readouterr().err


def test_main_strict_passes_for_complete_metadata(mocker: MockerFixture) -> None:
    mocker.patch(
        "superset.db_engine_specs.lint_metadata.get_all_engine_specs_ast",
        return_value=[{"engine_name": "MyDB", "metadata": COMPLETE_METADATA}],
    )
    mocker.patch("sys.argv", ["lint_metadata.py", "--strict"])

    assert main() == 0
