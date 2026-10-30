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
