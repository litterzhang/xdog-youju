"""Three grouped acquisition-parser checks using local synthetic material only."""

import io
import json
import re
import struct
import subprocess
import zipfile

from bs4 import BeautifulSoup
from docx import Document
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from openpyxl import Workbook
from openpyxl.styles import PatternFill
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from youju.onboarding import materials
from youju.onboarding.materials import parse_material

BASE = "https://jobs.example.org/notices/current.html"


def parse(body, suffix="html", key="version-1", media_type="application/octet-stream"):
    if isinstance(body, str):
        body = body.encode("utf-8")
    return parse_material(body, media_type=media_type, url=f"{BASE}.{suffix}", material_key=key)


def assert_document(document, key):
    assert set(document) - {"structure"} == {
        "format",
        "status",
        "title",
        "blocks",
        "tables",
        "links",
        "errors",
        "truncated",
    }
    assert document["status"] in {"ok", "partial", "unsupported", "failed"}
    json.dumps(document, allow_nan=False)
    for link in document["links"]:
        assert link["kind"] in {"page", "attachment", "asset", "form"}
        assert link["is_attachment"] == (link["kind"] == "attachment")
    blocks = {block["id"]: block for block in document["blocks"]}
    assert len(blocks) == len(document["blocks"])
    for region in document.get("structure", {}).get("regions", []):
        assert set(region["evidence_ids"]) <= set(blocks)
    assert all(key_.startswith(f"{key}:b") for key_ in blocks)
    for table in document["tables"]:
        assert table["id"].startswith(f"{key}:t")
        assert len(table["rows"]) == len(table["cell_evidence"])
        for row, references in zip(table["rows"], table["cell_evidence"], strict=True):
            assert len(row) == len(references)
            for text, reference in zip(row, references, strict=True):
                assert blocks[reference]["text"] == text
                assert blocks[reference]["locator"]


def errors(document):
    return {error["code"] for error in document["errors"]}


def office_bytes(document):
    stream = io.BytesIO()
    document.save(stream)
    return stream.getvalue()


def rewrite_zip(body, transform):
    output = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(body)) as source, zipfile.ZipFile(output, "w") as target:
        for name in source.namelist():
            target.writestr(name, transform(name, source.read(name)))
    return output.getvalue()


def test_html_original_evidence_discovery_sitemaps_and_limits(monkeypatch):
    # Source content may itself sit inside a legacy layout table.
    html = """<!doctype html><html><head><meta charset="utf-8"><base href="/jobs/">
    <title>招聘公告</title></head><body><table><tr><td><div id="oInfoContent">
    <p>报名截止 <strong>2026年10月31日</strong>。</p>
    <table><tr><th rowspan="2">岗位名称</th><th colspan="2">要求</th></tr>
    <tr><th>学历</th><th>人数</th></tr>
    <tr><td>管理助理</td><td>硕士</td><td>2</td></tr></table>
    <a href="notice?sign=1">下一篇</a><a href="/opaque?id=2" download>文件</a>
    <a href="/opaque?id=3">附件：岗位表</a><a href="data:application/pdf,xxx">忽略</a>
    <a href="https://outside.example.net/other">外域线索</a>
    <a href="http://127.0.0.1/internal">Host must reject this later</a>
    <iframe src="/files/a.PDF"></iframe>
    <object data="/opaque?id=4" type="application/pdf"></object>
    <embed src="//files.example.org/form.docx" type="application/msword">
    <script>do_not_execute()</script></div></td></tr></table></body></html>"""
    result = parse(html)
    assert_document(result, "version-1")
    assert result["status"] == "ok" and result["title"] == "招聘公告"
    assert any("报名截止" in block["text"] for block in result["blocks"])
    shell = parse(
        "<html><head><title>Loading jobs</title></head><body><script>load()</script></body></html>"
    )
    assert shell["title"] == "Loading jobs" and not shell["blocks"] and not shell["tables"]
    encoded = parse("<p>招聘岗位</p>".encode("gb18030"), media_type="text/html; charset=gb18030")
    assert encoded["blocks"][0]["text"] == "招聘岗位"
    fragmented = parse("<div>Before<p>Middle</p>After</div>")
    assert [block["text"] for block in fragmented["blocks"]] == ["Before", "Middle", "After"]
    assert not any("do_not_execute" in block["text"] for block in result["blocks"])
    table = result["tables"][0]
    assert table["rows"] == [
        ["岗位名称", "要求", "要求"],
        ["岗位名称", "学历", "人数"],
        ["管理助理", "硕士", "2"],
    ]
    assert table["cell_evidence"][0][0] == table["cell_evidence"][1][0]
    assert table["cell_evidence"][0][1] == table["cell_evidence"][0][2]
    soup = BeautifulSoup(html, "html.parser")
    for block in result["blocks"]:
        original = soup.select_one(block["locator"])
        assert original is not None
        assert block["text"] in re.sub(r"\s+", " ", original.get_text(" ", strip=True))
    links = {link["url"]: link for link in result["links"]}
    assert "https://jobs.example.org/jobs/notice?sign=1" in links
    assert "http://127.0.0.1/internal" in links  # Discovery does not grant approval.
    assert "https://outside.example.net/other" in links
    for url in ("/opaque?id=2", "/opaque?id=3", "/opaque?id=4", "/files/a.PDF"):
        assert links["https://jobs.example.org" + url]["is_attachment"]
    assert links["https://files.example.org/form.docx"]["is_attachment"]
    assert all(link["url"].startswith(("https://", "http://")) for link in result["links"])

    # Relationship kinds must survive inventory and URL deduplication; filenames
    # alone do not turn a script/image/form into a page or attachment.
    relationships = parse("""<html><head>
    <link rel="stylesheet" href="/style.aspx"><link rel="next" href="/next">
    <link rel="preload" as="image" href="/preloaded">
    <link rel="preload" as="document" href="/document">
    <script src="/script.pdf"></script></head><body>
    <img src="/image.pdf" alt="附件下载"><video src="/video" poster="/poster"></video>
    <source src="/source" srcset="/small 1x, /large 2x">
    <form action="/submit.docx"><button formaction="/button.pdf">下载</button></form>
    <iframe src="/frame"></iframe><embed src="/embedded" type="text/html">
    <object data="/object" type="application/pdf"></object>
    <object data="/object-image" type="image/svg+xml"></object>
    <area href="/map"><a href="/human.js">Human navigation</a>
    <img src="/shared" alt="Logo"><a href="/shared">Actual page</a>
    <a href="/reverse">Actual page first</a><img src="/reverse" alt="Logo">
    <script src="/shared-file"></script><a href="/shared-file" download>Document</a>
    <a href="/reverse-file" type="application/pdf">PDF</a><img src="/reverse-file">
    <form action="/shared-form"></form><a href="/shared-form">Page not submit</a>
    </body></html>""")
    assert_document(relationships, "version-1")
    by_path = {
        link["url"].removeprefix("https://jobs.example.org"): link
        for link in relationships["links"]
    }
    expected = {
        "asset": {
            "/style.aspx",
            "/preloaded",
            "/script.pdf",
            "/image.pdf",
            "/video",
            "/poster",
            "/source",
            "/small",
            "/large",
            "/object-image",
        },
        "form": {"/submit.docx", "/button.pdf"},
        "page": {
            "/next",
            "/document",
            "/frame",
            "/embedded",
            "/map",
            "/human.js",
            "/shared",
            "/reverse",
            "/shared-form",
        },
        "attachment": {"/object", "/shared-file", "/reverse-file"},
    }
    assert len(by_path) == sum(len(paths) for paths in expected.values())
    for kind, paths in expected.items():
        assert all(by_path[path]["kind"] == kind for path in paths)
    assert by_path["/shared"]["label"] == "Actual page"
    assert by_path["/reverse"]["label"] == "Actual page first"
    assert by_path["/shared-file"]["label"] == "Document"

    # Legacy sites use nested layout tables and empty spacer rows. Neither is
    # a second job table; a navigation downloads category is not an attachment.
    layout = """<html><body><table id="layout"><tr><td><table><tr><td>
    <ul id="nav"><li><a href="category.aspx?id=1">文档下载</a></li>
    <li><a href="form.docx">报名表</a></li></ul></td></tr></table></td></tr>
    <tr style="height:8px"></tr><tr><td><h1>招聘公告</h1>
    <p>报名截止2026年10月31日</p><table id="positions">
    <tr><th>岗位名称</th><th>人数</th></tr><tr style="height:8px"></tr>
    <tr><td>管理助理</td><td>1</td></tr></table></td></tr></table></body></html>"""
    normalized = parse(layout)
    assert_document(normalized, "version-1")
    assert normalized["status"] == "ok"
    assert len(normalized["tables"]) == 1
    assert normalized["tables"][0]["rows"] == [["岗位名称", "人数"], ["管理助理", "1"]]
    assert sum(block["text"] == "管理助理" for block in normalized["blocks"]) == 1
    assert sum(block["text"] == "报名截止2026年10月31日" for block in normalized["blocks"]) == 1
    assert not next(link for link in normalized["links"] if "category.aspx" in link["url"])[
        "is_attachment"
    ]
    assert next(link for link in normalized["links"] if link["url"].endswith(".docx"))[
        "is_attachment"
    ]
    original = BeautifulSoup(layout, "html.parser")
    for block in normalized["blocks"]:
        node = original.select_one(block["locator"])
        assert node is not None and block["text"] in re.sub(
            r"\s+", " ", node.get_text(" ", strip=True)
        )

    xml = """<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
    <url><loc>https://jobs.example.org/a?x=1&amp;y=2</loc></url>
    <url><loc>https://jobs.example.org/positions.xlsx</loc></url></urlset>"""
    sitemap = parse(xml, "xml")
    assert_document(sitemap, "version-1")
    assert sitemap["status"] == "ok" and len(sitemap["links"]) == 2
    assert sitemap["links"][0]["url"].endswith("?x=1&y=2")
    assert sitemap["links"][0]["kind"] == "page"
    assert sitemap["links"][1]["kind"] == "attachment"
    assert sitemap["links"][1]["is_attachment"]
    assert not sitemap["blocks"] and not sitemap["tables"]
    index = parse("<sitemapindex><sitemap><loc>/nested.xml</loc></sitemap></sitemapindex>", "xml")
    assert index["links"][0]["url"] == "https://jobs.example.org/nested.xml"
    unsafe = parse(
        '<!DOCTYPE urlset [<!ENTITY secret SYSTEM "file:///etc/passwd">]><urlset><url><loc>&secret;</loc></url></urlset>',
        "xml",
    )
    assert unsafe["status"] == "failed" and "UNSAFE_OR_INVALID_XML" in errors(unsafe)
    with monkeypatch.context() as patch:
        patch.setattr(materials, "MAX_INPUT_BYTES", 8)
        limited = parse(html)
        assert limited["status"] == "failed" and limited["truncated"]
    with monkeypatch.context() as patch:
        patch.setattr(materials, "MAX_LINKS", 1)
        limited = parse(html)
        assert len(limited["links"]) == 1 and limited["status"] == "partial"
        assert limited["truncated"] and "LINK_LIMIT" in errors(limited)
    with monkeypatch.context() as patch:
        patch.setattr(materials, "MAX_OUTPUT_BYTES", 600)
        limited = parse(html)
        assert limited["status"] == "failed" and "OUTPUT_LIMIT" in errors(limited)
        assert not limited["tables"] and not limited["blocks"]


def test_office_tables_formulas_hidden_content_and_zip_boundaries(monkeypatch):
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "岗位"
    sheet.append(["岗位名称", "人数", "学历"])
    sheet.append(["管理助理", 2, "硕士"])
    sheet.append([None, "=1+1", None])
    sheet.merge_cells("A2:A3")
    sheet.row_dimensions[3].hidden = True
    body = office_bytes(workbook)
    xlsx = parse(body, "xlsx", "xlsx-version")
    assert_document(xlsx, "xlsx-version")
    table = xlsx["tables"][0]
    assert table["rows"][1] == ["管理助理", "2", "硕士"]
    assert table["rows"][2] == ["管理助理", "=1+1", ""]
    assert table["cell_evidence"][1][0] == table["cell_evidence"][2][0]
    assert {"FORMULA_NO_CACHED_VALUE", "HIDDEN_CONTENT_INCLUDED"} <= errors(xlsx)
    cached_body = rewrite_zip(
        body,
        lambda name, data: (
            data.replace(b"<f>1+1</f><v></v>", b"<f>1+1</f><v>2</v>")
            if name == "xl/worksheets/sheet1.xml"
            else data
        ),
    )
    cached = parse(cached_body, "xlsx")
    assert cached["tables"][0]["rows"][2][1] == "2"
    assert "FORMULA_CACHED_VALUE" in errors(cached)
    with monkeypatch.context() as patch:
        patch.setattr(materials, "MAX_ROWS", 1)
        limited = parse(body, "xlsx")
        assert len(limited["tables"][0]["rows"]) == 1
        assert limited["truncated"] and limited["status"] == "partial"

    # Formatting beyond the bounds must not invent omitted data. Conversely,
    # real values (including zero/False) and formulas there must still hit limits.
    styled = sheet.cell(materials.MAX_ROWS + 1, materials.MAX_COLUMNS + 1)
    styled.fill = PatternFill(fill_type="solid", fgColor="EEEEEE")
    bounded = parse(office_bytes(workbook), "xlsx")
    assert bounded["tables"][0]["rows"] == xlsx["tables"][0]["rows"]
    assert not bounded["truncated"]
    assert errors(bounded) == errors(xlsx)
    for value in (0, False, "real out-of-bounds text", '=IF(1,"","")'):
        sheet.cell(2, materials.MAX_COLUMNS + 1).value = value
        limited = parse(office_bytes(workbook), "xlsx")
        assert limited["truncated"] and "COLUMN_LIMIT" in errors(limited)
    sheet.cell(2, materials.MAX_COLUMNS + 1).value = None
    sheet.cell(materials.MAX_ROWS + 1, 1).value = "real distant row"
    limited = parse(office_bytes(workbook), "xlsx")
    assert limited["truncated"] and "ROW_LIMIT" in errors(limited)
    merged_book = Workbook()
    merged_book.active["A1"] = "Merged heading"
    merged_book.active.merge_cells("A1:C2")
    merged = parse(office_bytes(merged_book), "xlsx")
    assert merged["tables"][0]["rows"] == [["Merged heading"] * 3] * 2
    assert len({key for row in merged["tables"][0]["cell_evidence"] for key in row}) == 1

    word = Document()
    word.add_paragraph("报名截止日期：2026年10月31日。")
    table = word.add_table(rows=3, cols=2)
    table.cell(0, 0).text = "岗位名称"
    table.cell(0, 1).text = "人数"
    table.cell(1, 0).text = "管理助理"
    table.cell(1, 1).text = "2"
    table.cell(1, 0).merge(table.cell(2, 0))
    table.cell(2, 1).text = "3"
    paragraph = word.add_paragraph()
    link = OxmlElement("w:hyperlink")
    relation = word.part.relate_to(
        "https://127.0.0.1/never-fetch",
        "http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink",
        is_external=True,
    )
    link.set(qn("r:id"), relation)
    run, text = OxmlElement("w:r"), OxmlElement("w:t")
    text.text = "官方链接"
    run.append(text)
    link.append(run)
    paragraph._p.append(link)
    docx = parse(office_bytes(word), "docx", "docx-version")
    assert_document(docx, "docx-version")
    assert docx["tables"][0]["rows"] == [["岗位名称", "人数"], ["管理助理", "2"], ["管理助理", "3"]]
    assert docx["tables"][0]["cell_evidence"][1][0] == docx["tables"][0]["cell_evidence"][2][0]
    assert any("报名截止日期" in block["text"] for block in docx["blocks"])
    assert any(block["text"] == "官方链接" for block in docx["blocks"])
    assert "EXTERNAL_RELATIONSHIP_IGNORED" in errors(docx)
    assert not docx["links"]  # Neither reads nor follows relationship targets.
    unsafe = rewrite_zip(
        office_bytes(word),
        lambda name, data: (
            b'<!DOCTYPE evil [<!ENTITY file SYSTEM "file:///etc/passwd">]><evil>&file;</evil>'
            if name == "word/document.xml"
            else data
        ),
    )
    assert "UNSAFE_OR_INVALID_XML" in errors(parse(unsafe, "docx"))
    bomb = io.BytesIO()
    with zipfile.ZipFile(bomb, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("word/document.xml", b"x" * 100000)
    rejected = parse(bomb.getvalue(), "docx")
    assert rejected["status"] == "failed" and rejected["truncated"]
    assert parse(b"not an Office container", "docx")["status"] == "failed"
    merged_bomb = rewrite_zip(
        body,
        lambda name, data: (
            data.replace(b'ref="A2:A3"', b'ref="A1:XFD1048576"')
            if name == "xl/worksheets/sheet1.xml"
            else data
        ),
    )
    rejected = parse(merged_bomb, "xlsx")
    assert rejected["status"] == "failed" and rejected["truncated"]

    # A tiny uncompressed BIFF2 workbook exercises legacy XLS without a writer dependency.
    def record(opcode, data):
        return struct.pack("<HH", opcode, len(data)) + data

    biff = record(0x0009, struct.pack("<HH", 0x0002, 0x0010))
    for ri, value in enumerate((b"Position", b"Assistant")):
        biff += record(
            0x0004, struct.pack("<HH", ri, 0) + b"\x00\x00\x00" + bytes([len(value)]) + value
        )
    biff += record(
        0x0006,
        struct.pack("<HH", 2, 0)
        + b"\x00\x00\x00"
        + struct.pack("<d", 2.0)
        + b"\x00\x03\x1e\x02\x00",
    )
    biff += record(0x000A, b"")
    xls = parse(biff, "xls", "xls-version")
    assert_document(xls, "xls-version")
    assert xls["status"] == "partial" and "FORMULA_CACHED_VALUE" in errors(xls)
    assert xls["tables"][0]["rows"] == [["Position"], ["Assistant"], ["2"]]


def test_pdf_evidence_no_text_encryption_and_bounded_legacy_doc(monkeypatch):
    writer = PdfWriter()
    page = writer.add_blank_page(width=300, height=300)
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    page[NameObject("/Resources")] = DictionaryObject(
        {NameObject("/Font"): DictionaryObject({NameObject("/F1"): writer._add_object(font)})}
    )
    stream = DecodedStreamObject()
    stream.set_data(b"BT /F1 12 Tf 20 200 Td (Position: Assistant) Tj ET")
    page[NameObject("/Contents")] = writer._add_object(stream)
    writer.add_blank_page(width=300, height=300)
    output = io.BytesIO()
    writer.write(output)
    pdf = parse(output.getvalue(), "pdf", "pdf-version")
    assert_document(pdf, "pdf-version")
    assert pdf["blocks"][0]["text"] == "Position: Assistant"
    assert pdf["blocks"][0]["locator"] == "pdf:page=1;text-line=1"
    assert "PDF_NO_TEXT" in errors(pdf) and pdf["status"] == "partial"
    with monkeypatch.context() as patch:
        patch.setattr(materials, "MAX_PAGES", 1)
        limited = parse(output.getvalue(), "pdf")
        assert "PAGE_LIMIT" in errors(limited) and limited["truncated"]
    with monkeypatch.context() as patch:
        patch.setattr(materials, "MAX_PDF_STREAM_BYTES", 8)
        limited = parse(output.getvalue(), "pdf")
        assert limited["truncated"] and limited["status"] == "failed"
    writer.encrypt("not-a-real-secret")
    encrypted_output = io.BytesIO()
    writer.write(encrypted_output)
    encrypted = parse(encrypted_output.getvalue(), "pdf")
    assert encrypted["status"] == "failed" and "ENCRYPTED_PDF" in errors(encrypted)
    assert parse(b"%PDF-1.7\ncorrupt", "pdf")["status"] == "failed"

    # Test the bounded converter contract without inventing a valid binary .doc.
    calls = []

    def converter(command, **kwargs):
        calls.append((command, kwargs))
        assert command[:5] == ["/usr/bin/antiword", "-t", "-m", "UTF-8.txt", "-w"]
        assert kwargs["stdin"] == subprocess.DEVNULL
        assert "shell" not in kwargs
        kwargs["stdout"].write("岗位名称 管理助理\n人数 2\n".encode())
        assert kwargs["start_new_session"]

        class Process:
            returncode = 0

            def wait(self, timeout):
                assert timeout == materials.ANTIWORD_TIMEOUT
                return self.returncode

        return Process()

    with monkeypatch.context() as patch:
        patch.setattr(materials.subprocess, "Popen", converter)
        doc = parse(b"synthetic-doc-input", "doc", "doc-version")
        assert_document(doc, "doc-version")
        assert len(calls) == 1 and doc["status"] == "partial"
        assert doc["blocks"][0]["text"] == "岗位名称 管理助理"
        assert doc["blocks"][0]["locator"] == "doc:antiword-text-line=1"
        assert "DOC_TEXT_ONLY" in errors(doc)
    with monkeypatch.context() as patch:
        killed = []

        class TimeoutProcess:
            pid = 123456

            def wait(self, timeout=None):
                if timeout is not None:
                    raise subprocess.TimeoutExpired("antiword", timeout)
                return -9

        patch.setattr(materials.subprocess, "Popen", lambda *args, **kwargs: TimeoutProcess())
        patch.setattr(materials.os, "killpg", lambda pid, sig: killed.append(pid))
        doc = parse(b"synthetic-doc-input", "doc")
        assert doc["status"] == "failed" and doc["truncated"]
        assert "DOC_CONVERSION_TIMEOUT" in errors(doc)
        assert killed == [123456]
    assert parse(b"plain unsupported data", "bin")["status"] == "unsupported"


def test_html_preserves_bounded_non_body_date_observations():
    document = parse_material(
        b'<html><head><meta property="article:modified_time" content="2025-03-21">'
        b'<meta name="unrelated" content="2020-01-01"></head><body><main>'
        b'<time datetime="2026-10-23T12:00:00+08:00">Updated</time>'
        b"<p>Recruitment</p></main></body></html>",
        media_type="text/html",
        url="https://example.org/notice",
        material_key="date-hints",
    )
    assert [(item["kind"], item["value"]) for item in document["date_hints"]] == [
        ("html_meta", "2025-03-21"),
        ("html_time", "2026-10-23T12:00:00+08:00"),
    ]
    assert all(item["locator"] for item in document["date_hints"])


def test_saved_http_date_observation_remains_bounded_and_independent_of_capture_clock():
    from youju.onboarding.acquisition import observe_http_date

    document = {"format": "html", "date_hints": [{"kind": "html_time", "value": "2025-01-01"}] * 64}
    observe_http_date(document, "Fri, 21 Mar 2025 08:00:00 GMT")
    assert len(document["date_hints"]) == 64
    assert document["date_hints"][-1]["kind"] == "http_last_modified"
    assert "captured_at" not in str(document)
    empty = {"format": "html"}
    for invalid in (None, "", "x" * 257):
        observe_http_date(empty, invalid)
    assert empty == {"format": "html"}
