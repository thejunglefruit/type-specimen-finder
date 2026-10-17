// SPDX-License-Identifier: GPL-3.0-or-later
// Copyright (C) 2026 The Jungle Fruit
//
// TypeSpecimen.jsx - Illustrator side of type-specimen-finder.
// Reads the <word>_illustrator.json written by specimen.py and builds a NEW document
// with one artboard per typeface: the word as live, editable text in that face,
// and a small caption with its name, licence and measured stem.
// It never touches the documents you already have open.
//
// File > Scripts > Other Script... and pick this file, then pick the JSON.
// From another program: run it with the JSON path as the first argument.

var TS_ARGS = (typeof arguments !== "undefined") ? arguments : [];
(function (args) {
    var ART_W = 1000, ART_H = 360, GAP = 60, MARGIN = 60;
    var WORD_SIZE = 150, CAPTION_SIZE = 14;
    var MAX_COLS = 14, PER_DOC = 14 * 34;   // 14 x 34 artboards fill about 14800 x 14300 pt of the canvas
    var interactive = !(args.length && args[0]);

    // ---- a small strict JSON reader (no eval: the file could come from anywhere)
    function parseJSON(src) {
        var i = 0;
        function ws() { while (i < src.length && " \t\r\n".indexOf(src.charAt(i)) >= 0) { i++; } }
        function fail(m) { throw new Error("Bad JSON at " + i + ": " + m); }
        function str() {
            var out = "", c;
            i++;
            while (i < src.length) {
                c = src.charAt(i++);
                if (c === '"') { return out; }
                if (c === "\\") {
                    c = src.charAt(i++);
                    if (c === "u") { out += String.fromCharCode(parseInt(src.substr(i, 4), 16)); i += 4; }
                    else if (c === "n") { out += "\n"; } else if (c === "t") { out += "\t"; }
                    else if (c === "r") { out += "\r"; } else if (c === "b") { out += "\b"; }
                    else if (c === "f") { out += "\f"; } else { out += c; }
                } else { out += c; }
            }
            fail("unterminated string");
        }
        function num() {
            var m = /^-?\d+(\.\d+)?([eE][+-]?\d+)?/.exec(src.substr(i));
            if (!m) { fail("number"); }
            i += m[0].length;
            return parseFloat(m[0]);
        }
        function val() {
            ws();
            var c = src.charAt(i), o, a;
            if (c === "{") {
                o = {}; i++; ws();
                if (src.charAt(i) === "}") { i++; return o; }
                while (true) {
                    ws(); if (src.charAt(i) !== '"') { fail("key"); }
                    var k = str(); ws();
                    if (src.charAt(i++) !== ":") { fail("colon"); }
                    o[k] = val(); ws();
                    c = src.charAt(i++);
                    if (c === "}") { return o; }
                    if (c !== ",") { fail("comma"); }
                }
            }
            if (c === "[") {
                a = []; i++; ws();
                if (src.charAt(i) === "]") { i++; return a; }
                while (true) {
                    a.push(val()); ws();
                    c = src.charAt(i++);
                    if (c === "]") { return a; }
                    if (c !== ",") { fail("comma"); }
                }
            }
            if (c === '"') { return str(); }
            if (src.substr(i, 4) === "true") { i += 4; return true; }
            if (src.substr(i, 5) === "false") { i += 5; return false; }
            if (src.substr(i, 4) === "null") { i += 4; return null; }
            return num();
        }
        var v = val(); ws();
        if (i !== src.length) { fail("trailing data"); }
        return v;
    }

    // ---- fonts: by PostScript name, then by family + style
    var byFamily = null;
    function findFont(face) {
        if (face.postscript) {
            try { return app.textFonts.getByName(face.postscript); } catch (e) {}
        }
        if (!byFamily) {
            byFamily = {};
            for (var n = 0; n < app.textFonts.length; n++) {
                var f = app.textFonts[n];
                byFamily[(f.family + "|" + f.style).toLowerCase()] = f;
            }
        }
        return byFamily[(face.family + "|" + face.style).toLowerCase()] || null;
    }
    function captionFont() {
        var names = ["ArialMT", "Arial-Regular", "MyriadPro-Regular", "Helvetica"];
        for (var n = 0; n < names.length; n++) {
            try { return app.textFonts.getByName(names[n]); } catch (e) {}
        }
        return null;
    }

    // ---- read the JSON
    var file = interactive ? File.openDialog("Pick the _illustrator.json written by specimen.py", "*.json") : new File(args[0]);
    if (!file) { return "cancelled"; }
    if (!file.exists) { throw new Error("Not found: " + file.fsName); }
    file.encoding = "UTF-8";
    file.open("r");
    var data = parseJSON(file.read());
    file.close();
    var word = data.word || "Specimen", faces = data.faces || [];
    if (!faces.length) { throw new Error("The JSON has no typefaces."); }

    // ---- fonts first, so the grid only holds the faces Illustrator can set
    var ready = [], missing = [];
    for (var k = 0; k < faces.length; k++) {
        var found = findFont(faces[k]);
        if (found) { ready.push({face: faces[k], font: found}); } else { missing.push(faces[k].name); }
    }
    if (!ready.length) { throw new Error("None of the typefaces is active in Illustrator."); }

    var black = new RGBColor(); black.red = 20; black.green = 20; black.blue = 20;
    var grey = new RGBColor(); grey.red = 120; grey.green = 120; grey.blue = 120;
    var capFont = captionFont();
    var docs = [], doc, layer, cols, left0, top0, slot = 0, placed = 0;

    // ---- the canvas is about 16000 pt across: a long list goes on several documents
    function startDoc(n) {
        cols = Math.min(MAX_COLS, Math.max(1, Math.ceil(Math.sqrt(n * (ART_H + GAP) / (ART_W + GAP)))));
        var rows = Math.ceil(n / cols);
        doc = app.documents.add(DocumentColorSpace.RGB, ART_W, ART_H);
        var r0 = doc.artboards[0].artboardRect;   // [left, top, right, bottom], y grows upwards
        left0 = (r0[0] + r0[2]) / 2 - (cols * (ART_W + GAP) - GAP) / 2;
        top0 = (r0[1] + r0[3]) / 2 + (rows * (ART_H + GAP) - GAP) / 2;
        layer = doc.layers[0];
        layer.name = "Specimens";
        docs.push(doc);
        slot = 0;
    }

    for (var q = 0; q < ready.length; q++) {
        if (q % PER_DOC === 0) { startDoc(Math.min(PER_DOC, ready.length - q)); }
        var face = ready[q].face, font = ready[q].font;
        var col = slot % cols, row = Math.floor(slot / cols);
        var L = left0 + col * (ART_W + GAP), T = top0 - row * (ART_H + GAP);
        var rect = [L, T, L + ART_W, T - ART_H], ab;
        if (slot === 0) { ab = doc.artboards[0]; ab.artboardRect = rect; } else { ab = doc.artboards.add(rect); }
        ab.name = face.name;

        var t = layer.textFrames.add();
        t.name = face.name;
        t.contents = word;
        var attrs = t.textRange.characterAttributes;
        attrs.textFont = font;
        attrs.size = WORD_SIZE;
        attrs.fillColor = black;
        // fit the word inside the margins, keeping its proportions
        var b = t.geometricBounds, w = b[2] - b[0], h = b[1] - b[3];
        var fit = Math.min((ART_W - 2 * MARGIN) / w, (ART_H - 2 * MARGIN - 40) / h, 1);
        if (fit < 1) { attrs.size = WORD_SIZE * fit; b = t.geometricBounds; w = b[2] - b[0]; h = b[1] - b[3]; }
        t.translate(L + MARGIN - b[0], (T - MARGIN) - b[1]);

        var c = layer.textFrames.add();
        c.name = face.name + " caption";
        c.contents = face.name + "   \u00b7   " + (face.licence || "licence unknown") +
                     (face.stem ? "   \u00b7   stem " + face.stem : "");
        var ca = c.textRange.characterAttributes;
        if (capFont) { ca.textFont = capFont; }
        ca.size = CAPTION_SIZE;
        ca.fillColor = grey;
        var cb = c.geometricBounds;
        c.translate(L + MARGIN - cb[0], (T - ART_H + MARGIN) - cb[3]);
        placed++;
        slot++;
    }
    for (var d = docs.length - 1; d >= 0; d--) {
        app.activeDocument = docs[d];
        app.executeMenuCommand("fitall");
    }

    var summary = placed + " typefaces on " + docs.length + (docs.length > 1 ? " documents" : " document") +
                  (missing.length ? "; not active in Illustrator (" + missing.length + "): " +
                   missing.slice(0, 20).join(", ") + (missing.length > 20 ? ", ..." : "") : "");
    if (interactive) { alert("Type specimen\n" + summary); }
    return summary;
})(TS_ARGS);
