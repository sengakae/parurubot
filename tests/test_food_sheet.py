import pytest

import utils.notes as notes

HEADER = "Area:,Name:,Region,Cuisine:,Price:,Link:,Notes:\n"
LONG_URL = (
    "https://www.google.com/maps/place/Kintaro+Ramen/@49.2879,-123.1336,17z"
    "/data=!3m1!4b1!4m6!3m5!1s0x548672!8m2!3d49.2879!4d-123.1310?entry=ttu&g_ep=EgoyMDI2"
)
SHORT_URL = "https://www.google.com/maps/place/Kintaro+Ramen/@49.2879,-123.1336,17z"
ROW = f'Vancouver,Kintaro,Japanese,Noodles,$,"{LONG_URL}",\n'
FULL = {"area": "Vancouver", "name": "Kintaro", "region": "Japanese",
        "cuisine": "Noodles", "price": "$", "url": LONG_URL, "notes": ""}


def test_sheet_rows_maps_headers_case_insensitively():
    assert notes.sheet_rows(HEADER + ROW) == [FULL]


def test_sheet_rows_strips_bom_from_first_header():
    assert notes.sheet_rows("﻿" + HEADER + ROW)[0]["area"] == "Vancouver"


def test_sheet_rows_strips_cells_and_skips_nameless_rows():
    text = " area: , NAME ,region,cuisine,price,link,notes\n  Richmond ,  Danbo ,,,,, AYCE \n,,,,,,\n"
    assert notes.sheet_rows(text) == [
        {"area": "Richmond", "name": "Danbo", "region": "", "cuisine": "",
         "price": "", "url": "", "notes": "AYCE"}
    ]


def test_sheet_rows_rejects_html():
    with pytest.raises(ValueError):
        notes.sheet_rows("<!DOCTYPE html><html><body>Sign in</body></html>")


def test_format_sheet_row_all_fields():
    assert notes.format_sheet_row(FULL) == (
        f"Kintaro: Japanese Noodles in Vancouver, price $. map: {SHORT_URL}"
    )


def test_format_sheet_row_missing_region():
    row = {**FULL, "region": ""}
    assert notes.format_sheet_row(row) == (
        f"Kintaro: Noodles in Vancouver, price $. map: {SHORT_URL}"
    )


def test_format_sheet_row_no_description():
    row = {**FULL, "region": "", "cuisine": "", "url": ""}
    assert notes.format_sheet_row(row) == "Kintaro in Vancouver, price $"


def test_format_sheet_row_name_only():
    row = {k: "" for k in FULL} | {"name": "Kintaro"}
    assert notes.format_sheet_row(row) == "Kintaro"


def test_format_sheet_row_includes_notes():
    row = {**FULL, "notes": "AYCE", "url": ""}
    assert notes.format_sheet_row(row) == "Kintaro: Japanese Noodles (AYCE) in Vancouver, price $"


def test_format_sheet_row_url_without_data_drops_query_only():
    row = {**FULL, "url": "https://maps.app.goo.gl/abc?g_st=ic"}
    assert notes.format_sheet_row(row).endswith("map: https://maps.app.goo.gl/abc")


def test_format_sheet_row_real_link_fits_search_preview():
    # search_personal_notes cuts each hit to 400 chars; a whole row must survive it
    long_name = {**FULL, "name": "Cafe de l'Orangerie Patisserie", "notes": "Late night only"}
    assert len(notes.format_sheet_row(long_name)) < 400


# --- sync_food_sheet ---

import hashlib
import types

import requests

URL = "https://docs.google.com/spreadsheets/d/x/export?format=csv"


def csv_bytes(*names):
    rows = "".join(f'Vancouver,{name},Japanese,Noodles,$,"{LONG_URL}",\n' for name in names)
    return (HEADER + rows).encode("utf-8")


class FakeCollection:
    def __init__(self):
        self.items = {}  # id -> (document, metadata)
        self.calls = []

    @staticmethod
    def _match(metadata, where):
        if where is None:
            return True
        if "$and" in where:
            return all(FakeCollection._match(metadata, w) for w in where["$and"])
        ((field, cond),) = where.items()
        value = cond["$eq"] if isinstance(cond, dict) else cond
        return metadata.get(field) == value

    def get(self, where=None, limit=None):
        ids = [i for i, (_, m) in self.items.items() if self._match(m, where)][:limit]
        return {"ids": ids, "metadatas": [self.items[i][1] for i in ids],
                "documents": [self.items[i][0] for i in ids]}

    def delete(self, ids=None, where=None):
        self.calls.append("delete")
        for i in list(self.items):
            if (ids is not None and i in ids) or (where is not None and self._match(self.items[i][1], where)):
                del self.items[i]

    def upsert(self, documents, ids, metadatas):
        self.calls.append("upsert")
        for d, i, m in zip(documents, ids, metadatas):
            self.items[i] = (d, m)


class FakeResponse:
    def __init__(self, content, status, encoding):
        self.content, self.status_code, self.encoding = content, status, encoding

    @property
    def text(self):
        return self.content.decode(self.encoding)


def fake_get(content=b"", status=200, encoding="ISO-8859-1", exc=None):
    calls = []

    def get(url, **kwargs):
        calls.append((url, kwargs))
        if exc:
            raise exc
        return FakeResponse(content, status, encoding)

    get.calls = calls
    return get


@pytest.fixture
def coll(monkeypatch):
    c = FakeCollection()
    monkeypatch.setattr(notes, "collection", c)
    return c


def sync(monkeypatch, **kwargs):
    get = fake_get(**kwargs)
    monkeypatch.setattr(notes.requests, "get", get)
    notes.sync_food_sheet(URL)
    return get


def test_sync_skips_when_url_unset(monkeypatch, coll):
    monkeypatch.setattr(notes, "config", types.SimpleNamespace())
    get = fake_get(content=csv_bytes("Kintaro"))
    monkeypatch.setattr(notes.requests, "get", get)
    notes.sync_food_sheet()
    assert get.calls == []
    assert coll.items == {}


def test_sync_loads_one_chunk_per_row(monkeypatch, coll):
    content = csv_bytes("Kintaro", "Danbo")
    get = sync(monkeypatch, content=content)
    assert sorted(coll.items) == ["vancouver_food_sheet_0", "vancouver_food_sheet_1"]
    doc, meta = coll.items["vancouver_food_sheet_0"]
    assert doc == notes.format_sheet_row(notes.sheet_rows(content.decode())[0])
    assert meta == {"source": "sheet:vancouver_food", "type": "sheet",
                    "file_hash": hashlib.md5(content).hexdigest(), "chunk": 0,
                    "area": "vancouver", "region": "japanese", "cuisine": "noodles", "price": "$"}
    assert get.calls[0][1]["timeout"] == 30


def test_sync_decodes_utf8_ignoring_guessed_encoding(monkeypatch, coll):
    content = (HEADER + "Vancouver,Kintaro,Japanese,Noodles,¢,,\n").encode("utf-8")
    sync(monkeypatch, content=content, encoding="ISO-8859-1")
    doc, _ = coll.items["vancouver_food_sheet_0"]
    assert "price ¢" in doc
    assert "Â" not in doc


def test_sync_unchanged_hash_does_nothing(monkeypatch, coll):
    sync(monkeypatch, content=csv_bytes("Kintaro"))
    coll.calls.clear()
    sync(monkeypatch, content=csv_bytes("Kintaro"))
    assert coll.calls == []


def test_sync_replaces_when_rows_shrink(monkeypatch, coll):
    sync(monkeypatch, content=csv_bytes("Kintaro", "Danbo", "Horin"))
    sync(monkeypatch, content=csv_bytes("Marutama"))
    assert list(coll.items) == ["vancouver_food_sheet_0"]
    assert coll.items["vancouver_food_sheet_0"][0].startswith("Marutama")


def test_sync_leaves_other_sources_alone(monkeypatch, coll):
    coll.items["ben_0"] = ("ben", {"source": "misc/ben.txt", "type": "txt"})
    sync(monkeypatch, content=csv_bytes("Kintaro"))
    assert "ben_0" in coll.items


def _assert_kept_after(monkeypatch, coll, **kwargs):
    sync(monkeypatch, content=csv_bytes("Kintaro", "Danbo"))
    before = dict(coll.items)
    sync(monkeypatch, **kwargs)
    assert coll.items == before


def test_sync_keeps_old_chunks_on_http_error(monkeypatch, coll):
    _assert_kept_after(monkeypatch, coll, content=b"oops", status=500)


def test_sync_keeps_old_chunks_on_request_exception(monkeypatch, coll):
    _assert_kept_after(monkeypatch, coll, exc=requests.Timeout())


def test_sync_keeps_old_chunks_on_html_body(monkeypatch, coll):
    _assert_kept_after(monkeypatch, coll, content=b"<!DOCTYPE html><html>Sign in</html>")


def test_sync_keeps_old_chunks_when_sheet_has_no_rows(monkeypatch, coll):
    _assert_kept_after(monkeypatch, coll, content=HEADER.encode())


def test_cleanup_skips_sheet_chunks(monkeypatch, coll, tmp_path):
    coll.items["vancouver_food_sheet_0"] = ("x", {"source": notes.SHEET_SOURCE, "type": "sheet"})
    coll.items["gone_0"] = ("y", {"source": "gone.txt", "type": "txt"})
    (tmp_path / "notes").mkdir()
    monkeypatch.chdir(tmp_path)
    notes.cleanup_deleted_files()
    assert list(coll.items) == ["vancouver_food_sheet_0"]


def test_sync_keeps_old_chunks_when_upsert_fails(monkeypatch, coll):
    sync(monkeypatch, content=csv_bytes("Kintaro", "Danbo"))
    before = dict(coll.items)

    def broken_upsert(**kwargs):
        raise RuntimeError("embedding failed")

    monkeypatch.setattr(coll, "upsert", broken_upsert)
    sync(monkeypatch, content=csv_bytes("Marutama"))
    assert coll.items == before


def test_sync_never_raises_on_unexpected_error(monkeypatch, coll):
    # an exception escaping sync would end the daily tasks.loop until restart
    sync(monkeypatch, content=csv_bytes("Kintaro"))
    before = dict(coll.items)
    sync(monkeypatch, exc=RuntimeError("surprise"))
    assert coll.items == before
