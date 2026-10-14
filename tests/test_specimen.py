# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (c) 2026 The Jungle Fruit
"""Tests with four OFL fonts bundled in tests/fonts (licence in tests/fonts/OFL.txt):
two heavy sans (kept), one medium sans (too light) and one heavy serif (serif)."""
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import specimen as S  # noqa: E402

FONTS = os.path.join(HERE, "fonts")


@pytest.fixture(scope="module")
def rows():
    return S.scan("Hello", [FONTS], verbose=False)["rows"]


def _by_name(rows):
    return {S.display_name(r): r for r in rows}


def test_scan_measures_every_face(rows):
    assert set(_by_name(rows)) == {"Archivo Expanded Black", "Bricolage Grotesque ExtraBold",
                                   "Instrument Sans Medium", "Merriweather Bold"}


def test_measures_follow_the_drawing(rows):
    m = _by_name(rows)
    # heavier drawing -> thicker stem, whatever the name says
    assert m["Archivo Expanded Black"]["stem"] > m["Bricolage Grotesque ExtraBold"]["stem"] \
        > m["Instrument Sans Medium"]["stem"]
    # the serif ratio separates the serif face from the sans ones
    assert m["Merriweather Bold"]["serif"] > 1.42
    assert all(m[n]["serif"] < 1.2 for n in m if n != "Merriweather Bold")


def test_labels_and_origin(rows):
    m = _by_name(rows)
    assert S.weight_label(m["Archivo Expanded Black"]) == "ultra"
    assert S.weight_label(m["Instrument Sans Medium"]) == "regular"   # not "bold"
    assert all(S.origin(r) == "folder" for r in rows)                  # scanned with --fonts-dir


def test_default_filters_keep_heavy_sans_only(tmp_path):
    chosen = S.main(["Hello", "--fonts-dir", FONTS, "--out", str(tmp_path), "--list-only"])
    assert sorted(S.display_name(r) for r in chosen) == \
        ["Archivo Expanded Black", "Bricolage Grotesque ExtraBold"]
    listing = (tmp_path / "Hello_list.txt").read_text(encoding="utf-8")
    assert "Archivo Expanded Black" in listing and "Merriweather" not in listing


def test_options_relax_the_filters(tmp_path):
    chosen = S.main(["Hello", "--fonts-dir", FONTS, "--out", str(tmp_path), "--list-only",
                     "--with-serif", "--all-weights"])
    assert len(chosen) == 4


def test_find_face_by_hand_typed_name(rows):
    face, _ = S.find_face("merriweather", rows)
    assert S.display_name(face) == "Merriweather Bold"
    face, _ = S.find_face("Bricolage-Grotesque", rows)
    assert S.display_name(face) == "Bricolage Grotesque ExtraBold"
    assert S.find_face("does not exist anywhere", rows)[0] is None


def test_read_selection(tmp_path):
    sel = tmp_path / "pick.txt"
    sel.write_text("# my shortlist\nArchivo, merriweather\n\nArchivo  # repeated\n", encoding="utf-8")
    assert S.read_selection(str(sel)) == ["Archivo", "merriweather"]


def test_licences_of_the_bundled_fonts(rows):
    m = {S.display_name(r): S.licence_of(r) for r in rows}
    assert all(L["licence"] == "SIL OFL 1.1" and L["kind"] == "open" for L in m.values())
    assert m["Archivo Expanded Black"]["home"] == "http://www.omnibus-type.com"
    assert m["Instrument Sans Medium"]["home"] == "https://www.fragtypefoundry.xyz"      # declared without scheme
    assert m["Merriweather Bold"]["home"] == "https://sorkintype.com"
    assert m["Merriweather Bold"]["licence_url"] == "http://sorkintype.com/eula.html"    # what the file declares
    assert all(L["embedding"] == "installable" for L in m.values())


@pytest.mark.parametrize("fstype,words", [(None, "not declared"), (0, "installable"), (2, "restricted"),
                                          (4, "preview & print"), (8, "editable"),
                                          (6, "preview & print"),                        # both bits: least restrictive wins
                                          (260, "preview & print (no subsetting)")])
def test_embedding_permissions(fstype, words):
    assert S.embedding(fstype) == words


@pytest.mark.parametrize("text,url,label,kind", [
    ("Microsoft supplied font. You may use this font to create, display, and print content", "",
     "Bundled with Windows (Microsoft)", "bundled"),
    ("This font is licensed under an End User License Agreement. All rights reserved.", "",
     "Commercial / proprietary", "commercial"),
    ("Licensed under the Apache License, Version 2.0", "http://www.apache.org/licenses/LICENSE-2.0", "Apache 2.0", "open"),
    ("Free for Personal Use", "", "Personal use only", "personal"),
    ("This font is free for non-commercial use only. Licensed under the SIL Open Font License?", "",
     "Personal use only", "personal"),                                  # the restriction wins over a vague OFL mention
    ("You may use this font to display and print content as permitted by the license terms for the product in which "
     "this font is included.", "", "Bundled with another product", "bundled"),
    ("", "", "Not declared in the file", "unknown"),
    ("Free for everyone, enjoy!", "", "See the licence text in the font", "unknown"),
])
def test_licence_classification(text, url, label, kind):
    L = S.licence_of({"lic_text": text, "lic_url": url, "fstype": 0})
    assert (L["licence"], L["kind"]) == (label, kind)


def test_web_url_only_makes_safe_links():
    assert S.web_url("www.foundry.com") == "https://www.foundry.com"
    assert S.web_url("foundry.com/fonts/x") == "https://foundry.com/fonts/x"
    assert S.web_url("javascript:alert(1)") == ""
    assert S.web_url("not a url") == ""
    assert S.domain("https://www.omnibus-type.com/fonts") == "omnibus-type.com"


def test_licence_csv(tmp_path):
    import csv
    S.main(["Hello", "--fonts-dir", FONTS, "--out", str(tmp_path), "--list-only"])
    with open(tmp_path / "Hello_licences.csv", encoding="utf-8-sig") as fh:
        table = list(csv.DictReader(fh))
    assert [t["family"] for t in table] == ["Archivo Expanded Black", "Bricolage Grotesque ExtraBold"]
    assert all(t["licence"] == "SIL OFL 1.1" and t["foundry_url"].startswith("http") for t in table)
    assert "SIL OFL 1.1" in (tmp_path / "Hello_list.txt").read_text(encoding="utf-8")


@pytest.mark.skipif(not S.find_browser(), reason="needs Chrome, Chromium or Edge")
def test_pdf_has_every_specimen_in_its_own_font(tmp_path):
    pytest.importorskip("pymupdf")
    import pymupdf
    S.main(["Hello", "--fonts-dir", FONTS, "--out", str(tmp_path), "--with-serif", "--all-weights"])
    pdf = tmp_path / "Hello_specimen.pdf"
    assert pdf.is_file()
    doc = pymupdf.open(str(pdf))
    assert doc.page_count == 5          # cover, alphabetical list, by-weight list, licences, one sheet
    rows4 = sorted(S.scan("Hello", [FONTS], verbose=False)["rows"],
                   key=lambda r: (S.display_name(r).lower(), r["stem"]))
    assert S.check_pdf(str(pdf), rows4, verbose=False) == []
    licences_page = doc[3]
    text = licences_page.get_text()
    assert "licences" in text.lower() and "SIL OFL 1.1" in text          # header is uppercased by CSS
    uris = {(l.get("uri") or "").rstrip("/") for p in doc for l in p.get_links()}   # Chrome adds a trailing slash
    assert "http://www.omnibus-type.com" in uris and "https://www.fragtypefoundry.xyz" in uris   # clickable
