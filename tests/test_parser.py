from app.parser import content_hash, parse_pdf
from tests.fixtures import V1_PDF, V2_PDF


def _by_number(parsed, number):
    return next(node for node in parsed.nodes if node.section_number == number)


def _parent(parsed, node):
    return next(item for item in parsed.nodes if item.key == node.parent_key)


def test_duplicate_error_codes_headings_have_distinct_parent_contexts():
    parsed = parse_pdf(V1_PDF)
    matches = [
        node
        for node in parsed.nodes
        if node.heading == "Error Codes" and node.section_number in {"4.2", "7.1"}
    ]
    assert len(matches) == 2
    assert matches[0].key != matches[1].key
    assert _parent(parsed, _by_number(parsed, "4.2")).heading == "Alarms and Safety Behavior"
    assert _parent(parsed, _by_number(parsed, "7.1")).heading == "Troubleshooting"


def test_missing_heading_level_and_out_of_order_headings_are_exposed():
    parsed = parse_pdf(V1_PDF)
    battery = _by_number(parsed, "2.1.1.1")
    assert battery.level == 4
    assert _parent(parsed, battery).section_number == "2.1"
    assert any(
        warning["code"] == "missing_heading_level"
        and warning["section"] == "2.1.1.1"
        for warning in parsed.warnings
    )
    operation_headings = [
        node.section_number
        for node in parsed.nodes
        if node.parent_key == _by_number(parsed, "3").key
    ]
    assert operation_headings.index("3.4") < operation_headings.index("3.3")
    assert any(warning["code"] == "out_of_order_heading" for warning in parsed.warnings)
    assert any(
        warning["code"] == "small_heading_font" and warning["section"] == "3.2"
        for warning in parsed.warnings
    )


def test_page_spanning_content_and_tables_are_preserved_in_v2():
    parsed_v1 = parse_pdf(V1_PDF)
    parsed = parse_pdf(V2_PDF)
    startup_v1 = _by_number(parsed_v1, "3.1")
    startup = _by_number(parsed, "3.1")
    regulatory = _by_number(parsed, "8.1")
    alarm_thresholds = _by_number(parsed, "4.3")
    cleaning = _by_number(parsed, "6.1")
    assert startup_v1.source_pages == [2, 3]
    assert startup.source_pages == [3]
    assert "Press and hold the power button for one second" in startup.body_text
    assert regulatory.source_pages == [6, 7]
    assert "validated against relevant clinical accuracy standards" in regulatory.body_text
    assert alarm_thresholds.source_pages == [4, 5]
    assert cleaning.source_pages == [5, 6]
    specs = _by_number(parsed, "2.1")
    error_codes = _by_number(parsed, "4.2")
    spec_table = next(block for block in specs.content_blocks if block["type"] == "table")
    code_table = next(block for block in error_codes.content_blocks if block["type"] == "table")
    assert len(spec_table["rows"]) == 7
    assert spec_table["rows"][1] == ["Pressure range", "0–299 mmHg"]
    assert code_table["rows"][-1][0] == "E6"


def test_whitespace_and_ligature_normalization_preserve_meaningful_hash_changes():
    assert content_hash("Battery Life", "Four  AA\nbatteries") == content_hash(
        "Battery Life", "Four AA batteries"
    )
    assert content_hash("Cuff", "office") == content_hash("Cuﬀ", "office")
    assert content_hash("Limit", "250 cycles") != content_hash("Limit", "300 cycles")
