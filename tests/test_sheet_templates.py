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


# --- sync_all_sheets ---

from tests.test_food_sheet import FakeCollection, FakeResponse  # noqa: E402

FILMS = {**TOY, "url": "https://example.com/films.csv",
         "columns": {"film": "title", "director": "author", "year": "year"}}


@pytest.fixture
def coll(monkeypatch):
    c = FakeCollection()
    monkeypatch.setattr(notes, "collection", c)
    return c


@pytest.fixture
def get_calls(monkeypatch):
    bodies = {TOY["url"]: b"Title,Author,Year\nDune,Herbert,1965\n",
              FILMS["url"]: b"Film,Director,Year\nAlien,Scott,1979\n"}
    calls = []

    def get(url, **kwargs):
        calls.append(url)
        return FakeResponse(bodies[url], 200, "utf-8")

    monkeypatch.setattr(notes.requests, "get", get)
    return calls


def test_sync_all_syncs_each_template(tmp_path, coll, get_calls):
    write(tmp_path, "books.json", TOY)
    write(tmp_path, "films.json", FILMS)
    notes.sync_all_sheets(tmp_path)
    assert coll.items["books_sheet_0"][1]["source"] == "sheet:books"
    assert coll.items["films_sheet_0"][1]["source"] == "sheet:films"
    assert coll.items["films_sheet_0"][0] == "Alien by Scott (1979)"
    assert coll.items["books_sheet_0"][1]["author"] == "herbert"


def test_sync_all_removes_deleted_template(tmp_path, coll, get_calls):
    coll.items["old_sheet_0"] = ("x", {"source": "sheet:old", "type": "sheet"})
    coll.items["ben_0"] = ("y", {"source": "misc/ben.txt", "type": "txt"})
    write(tmp_path, "books.json", TOY)
    notes.sync_all_sheets(tmp_path)
    assert "old_sheet_0" not in coll.items
    assert "ben_0" in coll.items


def test_sync_all_keeps_entries_of_invalid_template(tmp_path, coll, get_calls):
    coll.items["broken_sheet_0"] = ("x", {"source": "sheet:broken", "type": "sheet"})
    write(tmp_path, "broken.json", "{")
    notes.sync_all_sheets(tmp_path)
    assert "broken_sheet_0" in coll.items


def test_sync_all_bad_template_does_not_stop_others(tmp_path, coll, get_calls):
    write(tmp_path, "bad.json", "{")
    write(tmp_path, "books.json", TOY)
    notes.sync_all_sheets(tmp_path)
    assert "books_sheet_0" in coll.items


def test_sync_all_missing_folder_deletes_nothing(tmp_path, coll, get_calls):
    coll.items["books_sheet_0"] = ("x", {"source": "sheet:books", "type": "sheet"})
    notes.sync_all_sheets(tmp_path / "nope")
    assert "books_sheet_0" in coll.items
    assert get_calls == []


def test_sync_all_never_raises(tmp_path, monkeypatch, coll):
    def boom(folder):
        raise RuntimeError("surprise")

    monkeypatch.setattr(notes, "load_templates", boom)
    notes.sync_all_sheets(tmp_path)


# --- review fixes ---

@pytest.mark.parametrize("data", [
    {**TOY, "columns": ["title"]},
    {**TOY, "row": ["{title}", 5]},
    {**TOY, "required": True},
    {**TOY, "transforms": ["title"]},
    {**TOY, "columns": {"title": ["title"]}},
    {**TOY, "fields": "author"},
])
def test_load_template_rejects_wrong_types(tmp_path, data):
    with pytest.raises(ValueError):
        notes.load_template(write(tmp_path, "bad.json", data))


@pytest.mark.parametrize("reserved", ["type", "source", "file_hash", "template_hash", "chunk"])
def test_load_template_rejects_reserved_field_names(tmp_path, reserved):
    data = {**TOY, "columns": {**TOY["columns"], "kind": reserved}, "fields": [reserved]}
    with pytest.raises(ValueError):
        notes.load_template(write(tmp_path, "bad.json", data))


def test_load_template_normalizes_column_headers(tmp_path):
    data = {**TOY, "columns": {"Title:": "title", " AUTHOR ": "author", "year": "year"}}
    loaded = notes.load_template(write(tmp_path, "toy.json", data))
    rows = notes.sheet_rows("Title:,Author,Year\nDune,Herbert,1965\n", loaded)
    assert rows == [{"title": "Dune", "author": "Herbert", "year": "1965"}]


def test_sync_all_wrong_type_template_does_not_stop_others(tmp_path, coll, get_calls):
    write(tmp_path, "bad.json", {**TOY, "columns": ["title"]})
    write(tmp_path, "books.json", TOY)
    notes.sync_all_sheets(tmp_path)
    assert "books_sheet_0" in coll.items


def test_sync_reapplies_when_template_changes(tmp_path, coll, get_calls):
    write(tmp_path, "books.json", TOY)
    notes.sync_all_sheets(tmp_path)
    write(tmp_path, "books.json", {**TOY, "row": ["{title}", " - {author}"]})
    notes.sync_all_sheets(tmp_path)
    assert coll.items["books_sheet_0"][0] == "Dune - Herbert"
