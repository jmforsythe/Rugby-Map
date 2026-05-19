"""Tests for custom map bonus import packs."""

import json
from pathlib import Path

import pytest

from rugby.analysis.convert_projected_import import convert_markdown
from rugby.custom_map_imports import (
    IMPORTS_DIR,
    assign_all_leagues_colors,
    assign_league_colors,
    load_all_imports,
    spec_to_js_pack,
    tiers_dict_to_spec,
    validate_import_spec,
    write_bonus_imports_js,
)
from rugby.maps import COLOR_PALETTE

FIXTURE_MD = Path(__file__).parent / "fixtures" / "projected_tier_sample.md"


class TestValidateImportSpec:
    def test_valid_minimal(self):
        spec = {
            "schema_version": 1,
            "id": "test-pack",
            "label": "Test Pack",
            "season": "2099-2100",
            "tiers": [
                {
                    "tier": 5,
                    "leagues": [{"name": "League A", "teams": ["Alpha RFC"]}],
                }
            ],
        }
        validate_import_spec(spec, source="test")

    def test_rejects_missing_id(self):
        with pytest.raises(ValueError, match="missing id"):
            validate_import_spec(
                {"schema_version": 1, "label": "X", "tiers": [{"tier": 1, "leagues": []}]},
                source="t",
            )


class TestConvertProjectedImport:
    def test_markdown_to_json(self, tmp_path: Path):
        out = convert_markdown(
            FIXTURE_MD,
            import_id="sample-projection",
            label="Sample projection",
            output=tmp_path / "sample-projection.json",
        )
        spec = json.loads(out.read_text(encoding="utf-8"))
        assert spec["id"] == "sample-projection"
        assert spec["season"] == "unknown"
        assert len(spec["tiers"]) == 1
        assert spec["tiers"][0]["tier"] == 5
        leagues = spec["tiers"][0]["leagues"]
        assert leagues[0]["name"] == "National League 1 West"
        assert leagues[0]["teams"] == ["Alpha RFC", "Beta RFC"]


class TestBuildJsPayload:
    def test_assigns_colors(self):
        spec = {
            "schema_version": 1,
            "id": "colors",
            "label": "Colors",
            "tiers": [
                {
                    "tier": 5,
                    "leagues": [
                        {"name": "League A", "teams": ["A"]},
                        {"name": "Unassigned", "teams": ["B"]},
                    ],
                }
            ],
        }
        pack = spec_to_js_pack(spec)
        assert pack["tiers"][0]["id"] == "colors_5"
        assert pack["tiers"][0]["leagues"][0]["color"].startswith("#")
        assert pack["tiers"][0]["leagues"][1]["name"] == "Unassigned"


class TestAssignLeagueColors:
    def test_uses_map_builder_sort_not_casefold(self):
        leagues = [
            {"name": "Counties 1 adm Lancashire & Cheshire"},
            {"name": "Counties 1 Cumbria"},
        ]
        assign_league_colors(leagues, 7)
        cumbria = next(lg for lg in leagues if "Cumbria" in lg["name"])
        adm = next(lg for lg in leagues if "adm" in lg["name"])
        assert cumbria["color"] == COLOR_PALETTE[6]
        assert adm["color"] == COLOR_PALETTE[7]


class TestAssignAllLeaguesColors:
    def test_pyramid_leagues_keep_pyramid_colours_when_merit_sorts_first(self):
        pyramid = [{"name": "Counties 4 Hampshire", "teams": ["A"]}]
        assign_league_colors(pyramid, 10)
        pyr_color = pyramid[0]["color"]

        combined = [
            {"name": "CANDY 1", "teams": ["M"]},
            {"name": "Counties 4 Hampshire", "teams": ["A"]},
        ]
        assign_all_leagues_colors(combined, 10, pyramid)

        assert combined[1]["color"] == pyr_color
        assert combined[0]["color"] != pyr_color


class TestWriteBonusImportsJs:
    def test_writes_empty_array(self, tmp_path: Path):
        empty_dir = tmp_path / "imports"
        empty_dir.mkdir()
        write_bonus_imports_js(tmp_path / "out", directory=empty_dir)
        text = (tmp_path / "out" / "bonus_imports.js").read_text(encoding="utf-8")
        assert "var BONUS_IMPORTS = [];" in text

    def test_writes_packs_from_directory(self, tmp_path: Path):
        imports_dir = tmp_path / "imports"
        imports_dir.mkdir()
        spec = tiers_dict_to_spec(
            {5: [("National League 1 West", ["Alpha RFC", "Beta RFC"])]},
            import_id="demo",
            label="Demo",
            season="2099-2100",
        )
        (imports_dir / "demo.json").write_text(json.dumps(spec), encoding="utf-8")
        write_bonus_imports_js(tmp_path / "out", directory=imports_dir)
        text = (tmp_path / "out" / "bonus_imports.js").read_text(encoding="utf-8")
        assert "var BONUS_IMPORTS = [" in text
        assert "Alpha RFC" in text
        assert "Demo" in text

    def test_load_all_imports_skips_invalid(self, tmp_path: Path):
        imports_dir = tmp_path / "imports"
        imports_dir.mkdir()
        (imports_dir / "bad.json").write_text("{}", encoding="utf-8")
        (imports_dir / "good.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "id": "good",
                    "label": "Good",
                    "tiers": [{"tier": 1, "leagues": [{"name": "L", "teams": ["T"]}]}],
                }
            ),
            encoding="utf-8",
        )
        loaded = load_all_imports(imports_dir)
        assert len(loaded) == 1
        assert loaded[0]["id"] == "good"


def test_imports_dir_constant():
    assert IMPORTS_DIR.parent / "custom_map_imports" == IMPORTS_DIR
