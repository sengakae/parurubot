import json
from pathlib import Path

import pytest

import utils.notes as notes

TOY = {"url": "https://example.com/x.csv",
       "columns": {"title": "title", "author": "author", "year": "year"},
       "required": ["title"],
       "row": ["{title}", " by {author}", " ({year})"],
       "fields": ["author"]}


def write(folder: Path, name: str, data, encoding="utf-8") -> Path:
    path = folder / name
    path.write_text(data if isinstance(data, str) else json.dumps(data), encoding=encoding)
    return path


def test_render_row_toy_template():
    row = {"title": "Dune", "author": "Herbert", "year": "1965"}
    assert notes.render_row(row, TOY) == "Dune by Herbert (1965)"
    assert notes.render_row({**row, "author": ""}, TOY) == "Dune (1965)"


def test_render_row_value_with_braces():
    assert notes.render_row({"title": "{Cafe}", "author": "", "year": ""}, TOY) == "{Cafe}"


def test_sheet_rows_toy_template():
    text = "Title:,Author,Year,Extra\nDune,Herbert,1965,x\n,Nobody,2000,\n"
    assert notes.sheet_rows(text, TOY) == [{"title": "Dune", "author": "Herbert", "year": "1965"}]


def test_sheet_rows_missing_required_column_raises():
    with pytest.raises(ValueError):
        notes.sheet_rows("Author,Year\nHerbert,1965\n", TOY)


def test_load_template_accepts_bom(tmp_path):
    loaded = notes.load_template(write(tmp_path, "toy.json", TOY, encoding="utf-8-sig"))
    assert loaded == {**TOY, "transforms": {}}


@pytest.mark.parametrize("data", [
    "{not json",
    {k: v for k, v in TOY.items() if k != "url"},
    {**TOY, "url": ""},
    {**TOY, "required": []},
    {**TOY, "row": ["{titel}"]},
    {**TOY, "fields": ["publisher"]},
    {**TOY, "transforms": {"publisher": "shorten_maps_url"}},
    {**TOY, "transforms": {"title": "nope"}},
])
def test_load_template_rejects(tmp_path, data):
    with pytest.raises(ValueError):
        notes.load_template(write(tmp_path, "bad.json", data))


def test_csv_export_url_rewrites_edit_link():
    sheet = "https://docs.google.com/spreadsheets/d/1-unWLt6P7gi_U09Oz6W8G4I70xafEya_9HL8-5PyxLw"
    assert notes.csv_export_url(sheet + "/edit?usp=sharing") == sheet + "/export?format=csv"
    assert notes.csv_export_url("https://example.com/x.csv") == "https://example.com/x.csv"


def test_load_templates_ignores_non_json(tmp_path):
    write(tmp_path, "good.json", TOY)
    write(tmp_path, "bad.json", "{")
    write(tmp_path, "README.md", "# templates")
    loaded = notes.load_templates(tmp_path)
    assert set(loaded) == {"good", "bad"}
    assert loaded["bad"] is None
    assert loaded["good"]["url"] == TOY["url"]


def test_load_templates_missing_folder(tmp_path):
    assert notes.load_templates(tmp_path / "nope") == {}
