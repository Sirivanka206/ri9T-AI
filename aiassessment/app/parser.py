import hashlib
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import fitz


SECTION_RE = re.compile(r"^(\d+(?:\.\d+)*)\.?\s+(.+)$")
LIST_RE = re.compile(r"^\s*(?:\d+[.)]|[-*•‣])\s+")


def normalize_for_hash(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value)
    return " ".join(normalized.split())


def content_hash(heading: str, body_text: str) -> str:
    canonical = f"{normalize_for_hash(heading)}\n{normalize_for_hash(body_text)}"
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass
class ParsedNode:
    key: str
    heading: str
    section_number: str | None
    level: int
    parent_key: str | None
    body_text: str = ""
    raw_text: str = ""
    content_blocks: list[dict[str, Any]] = field(default_factory=list)
    source_pages: list[int] = field(default_factory=list)

    @property
    def content_hash(self) -> str:
        return content_hash(self.heading, self.body_text)


@dataclass
class ParsedDocument:
    title: str
    nodes: list[ParsedNode]
    raw_text: str
    warnings: list[dict[str, Any]]


@dataclass
class _TextBlock:
    page: int
    y0: float
    y1: float
    text: str
    raw_text: str
    spans: list[dict[str, Any]]
    bold: bool
    font_size: float


@dataclass
class _TableRegion:
    page: int
    y0: float
    y1: float
    columns: list[str]
    rows: list[list[str]]
    text: str


def _clean_text(value: str) -> str:
    return unicodedata.normalize("NFKC", value).replace("\u00ad", "").strip()


def _extract_page_blocks(page: fitz.Page, page_number: int) -> list[_TextBlock]:
    result: list[_TextBlock] = []
    page_dict = page.get_text("dict", sort=True)
    for block in page_dict["blocks"]:
        if "lines" not in block:
            continue
        lines = block["lines"]
        raw_text = "\n".join(
            "".join(span["text"] for span in line["spans"]) for line in lines
        )
        spans = [span for line in lines for span in line["spans"]]
        text = _clean_text(raw_text).replace("\n", " ")
        if not text:
            continue
        result.append(
            _TextBlock(
                page=page_number,
                y0=float(block["bbox"][1]),
                y1=float(block["bbox"][3]),
                text=text,
                raw_text=raw_text,
                spans=spans,
                bold=any("Bold" in span["font"] or span["flags"] & 16 for span in spans),
                font_size=max((float(span["size"]) for span in spans), default=0.0),
            )
        )
    return sorted(result, key=lambda item: (item.y0, min((s["bbox"][0] for s in item.spans), default=0)))


def _heading(block: _TextBlock) -> tuple[str, str] | None:
    match = SECTION_RE.match(block.text)
    if not match or not (block.bold or block.font_size >= 12.5):
        return None
    return match.group(1), match.group(2).strip()


def _line_groups(page: fitz.Page) -> list[tuple[float, list[dict[str, Any]]]]:
    lines: list[tuple[float, dict[str, Any]]] = []
    for block in page.get_text("dict")["blocks"]:
        for line in block.get("lines", []):
            for span in line["spans"]:
                lines.append((float(line["bbox"][1]), span))
    lines.sort(key=lambda item: (item[0], float(item[1]["bbox"][0])))
    groups: list[tuple[float, list[dict[str, Any]]]] = []
    for y, span in lines:
        if groups and abs(groups[-1][0] - y) <= 1.5:
            groups[-1][1].append(span)
        else:
            groups.append((y, [span]))
    return groups


def _table_regions(
    page: fitz.Page, page_number: int, blocks: list[_TextBlock]
) -> list[_TableRegion]:
    line_groups = _line_groups(page)
    rows_with_cells: list[tuple[float, list[tuple[float, str]]]] = []
    for y, spans in line_groups:
        cells = [
            (float(span["bbox"][0]), _clean_text(span["text"]))
            for span in sorted(spans, key=lambda span: float(span["bbox"][0]))
            if _clean_text(span["text"])
        ]
        rows_with_cells.append((y, cells))

    regions: list[_TableRegion] = []
    header_specs = (
        (["Parameter", "Value"], ["Parameter", "Value"]),
        (
            ["Code", "Meaning", "Device Behavior"],
            ["Code", "Meaning", "Device Behavior"],
        ),
    )
    for row_index, (header_y, cells) in enumerate(rows_with_cells):
        joined = " ".join(text for _, text in cells).casefold()
        spec = next(
            (
                entry
                for entry in header_specs
                if all(token.casefold() in joined for token in entry[0])
            ),
            None,
        )
        if spec is None:
            continue
        columns = spec[1]
        next_heading_y = next(
            (
                block.y0
                for block in blocks
                if block.y0 > header_y + 1
                and _heading(block) is not None
            ),
            float("inf"),
        )
        data_rows: list[list[str]] = []
        if columns[0] == "Parameter":
            for y, row_cells in rows_with_cells[row_index + 1 :]:
                if y >= next_heading_y:
                    break
                left = [text for x, text in row_cells if x < 150]
                right = [text for x, text in row_cells if x >= 150]
                if left or right:
                    data_rows.append([" ".join(left), " ".join(right)])
        else:
            current: list[str] | None = None
            for y, row_cells in rows_with_cells[row_index + 1 :]:
                if y >= next_heading_y:
                    break
                cells_by_column = [
                    [text for x, text in row_cells if x < 70],
                    [text for x, text in row_cells if 70 <= x < 210],
                    [text for x, text in row_cells if x >= 210],
                ]
                first = " ".join(cells_by_column[0])
                if re.fullmatch(r"E\d+", first):
                    if current:
                        data_rows.append(current)
                    current = ["", "", ""]
                if current is not None:
                    for index, pieces in enumerate(cells_by_column):
                        if pieces:
                            current[index] = " ".join(
                                part for part in (current[index], " ".join(pieces)) if part
                            )
            if current:
                data_rows.append(current)
        if not data_rows:
            continue
        rendered_rows = [
            f"{column}: {value}"
            for row in data_rows
            for column, value in zip(columns, row)
            if value
        ]
        regions.append(
            _TableRegion(
                page=page_number,
                y0=header_y,
                y1=next_heading_y,
                columns=columns,
                rows=data_rows,
                text="; ".join(rendered_rows),
            )
        )
    return regions


def parse_pdf(path: str | Path) -> ParsedDocument:
    source = Path(path)
    if not source.is_file():
        raise FileNotFoundError(f"PDF does not exist: {source}")
    try:
        pdf = fitz.open(source)
    except (fitz.FileDataError, RuntimeError) as exc:
        raise ValueError(f"Could not open PDF {source.name}: {exc}") from exc
    if pdf.is_encrypted:
        raise ValueError(f"Encrypted PDFs are not supported: {source.name}")

    all_blocks: dict[int, list[_TextBlock]] = {}
    all_tables: dict[int, list[_TableRegion]] = {}
    page_texts: list[str] = []
    warnings: list[dict[str, Any]] = []
    for page_index, page in enumerate(pdf):
        page_number = page_index + 1
        text = page.get_text("text", sort=True)
        page_texts.append(text)
        if "\ufffd" in text:
            warnings.append(
                {
                    "code": "unmapped_pdf_glyph",
                    "page": page_number,
                    "message": (
                        "Selectable text contains replacement characters from the PDF font map; "
                        "the source text is retained and affected values require visual review."
                    ),
                }
            )
        blocks = _extract_page_blocks(page, page_number)
        all_blocks[page_number] = blocks
        all_tables[page_number] = _table_regions(page, page_number, blocks)
        if len(text.strip()) < 30:
            warnings.append(
                {
                    "code": "low_text_page",
                    "page": page_number,
                    "message": "Page has little selectable text; OCR is not configured.",
                }
            )
        if page.get_images():
            warnings.append(
                {
                    "code": "images_not_extracted",
                    "page": page_number,
                    "message": "Page contains images; image content is preserved only by page reference.",
                }
            )

    first_blocks = all_blocks.get(1, [])
    title_blocks = [
        block
        for block in first_blocks[:3]
        if block.font_size >= 20 and not _heading(block)
    ]
    title = " ".join(block.text for block in title_blocks).strip()
    if not title:
        title = source.stem.replace("_", " ")
        warnings.append(
            {
                "code": "title_not_detected",
                "page": 1,
                "message": "No large-font document title was detected; filename used.",
            }
        )

    root_key = "document-title"
    nodes = [
        ParsedNode(
            key=root_key,
            heading=title,
            section_number=None,
            level=0,
            parent_key=None,
            source_pages=[1],
        )
    ]
    latest_by_number: dict[str, ParsedNode] = {}
    key_counts: dict[str, int] = {}
    previous_sibling_number: dict[str | None, int] = {}
    previous_page_node: ParsedNode | None = None
    for page_number in range(1, len(pdf) + 1):
        blocks = all_blocks[page_number]
        tables = all_tables[page_number]
        events: list[tuple[float, int, Any]] = []
        for table in tables:
            events.append((table.y0, 0, table))
        for block_index, block in enumerate(blocks):
            if any(table.y0 <= block.y0 < table.y1 for table in tables):
                continue
            events.append((block.y0, 1, block))
        for _, _, event in sorted(events, key=lambda item: (item[0], item[1])):
            if isinstance(event, _TableRegion):
                table = event
                if not nodes:
                    continue
                current = nodes[-1]
                table_block = {
                    "type": "table",
                    "columns": table.columns,
                    "rows": table.rows,
                    "text": table.text,
                    "page": page_number,
                }
                _append_content(current, table_block, table.text, table.text, page_number)
                continue
            block = event
            match = _heading(block)
            if page_number == 1 and block in title_blocks:
                continue
            if match:
                number, heading_text = match
                parts = number.split(".")
                parent_key: str | None = None
                for prefix_length in range(len(parts) - 1, 0, -1):
                    candidate = ".".join(parts[:prefix_length])
                    parent = latest_by_number.get(candidate)
                    if parent is not None:
                        parent_key = parent.key
                        if prefix_length < len(parts) - 1:
                            warnings.append(
                                {
                                    "code": "missing_heading_level",
                                    "page": page_number,
                                    "section": number,
                                    "message": (
                                        f"Missing section {'.'.join(parts[:-1])}; "
                                        f"attached to nearest existing ancestor {candidate}."
                                    ),
                                }
                            )
                        break
                if parent_key is None:
                    parent_key = root_key
                    if len(parts) > 1:
                        warnings.append(
                            {
                                "code": "missing_heading_parent",
                                "page": page_number,
                                "section": number,
                                "message": "No numbered parent heading exists; attached to document title.",
                            }
                        )
                sibling_number = int(parts[-1])
                previous = previous_sibling_number.get(parent_key)
                if previous is not None and sibling_number < previous:
                    warnings.append(
                        {
                            "code": "out_of_order_heading",
                            "page": page_number,
                            "section": number,
                            "message": "Section numbering is not in ascending source order.",
                        }
                    )
                previous_sibling_number[parent_key] = sibling_number
                key_counts[number] = key_counts.get(number, 0) + 1
                node_key = f"{number}:{key_counts[number]}"
                node = ParsedNode(
                    key=node_key,
                    heading=heading_text,
                    section_number=number,
                    level=len(parts),
                    parent_key=parent_key,
                )
                nodes.append(node)
                latest_by_number[number] = node
                previous_page_node = node
                if block.font_size < 12.5 and len(parts) < 4:
                    warnings.append(
                        {
                            "code": "small_heading_font",
                            "page": page_number,
                            "section": number,
                            "message": (
                                f"Bold numbered heading uses {block.font_size:.1f} pt; "
                                "recognized by typography plus section numbering."
                            ),
                        }
                    )
                continue
            if not block.text:
                continue
            target = previous_page_node or nodes[0]
            if target is nodes[0]:
                warnings.append(
                    {
                        "code": "content_before_first_section",
                        "page": page_number,
                        "message": "Unclassified text was preserved under the document title.",
                    }
                )
            kind = "list" if LIST_RE.match(block.text) else "paragraph"
            content = {
                "type": kind,
                "text": block.text,
                "page": page_number,
            }
            _append_content(target, content, block.text, block.raw_text, page_number)

    if len(nodes) == 1:
        warnings.append(
            {
                "code": "no_headings_detected",
                "page": 1,
                "message": "No numbered headings were detected in selectable PDF text.",
            }
        )
    return ParsedDocument(
        title=title,
        nodes=nodes,
        raw_text="\n".join(page_texts),
        warnings=warnings,
    )


def _append_content(
    node: ParsedNode, block: dict[str, Any], text: str, raw_text: str, page: int
) -> None:
    node.content_blocks.append(block)
    node.body_text = "\n".join(part for part in (node.body_text, text) if part)
    node.raw_text = "\n".join(part for part in (node.raw_text, raw_text) if part)
    if page not in node.source_pages:
        node.source_pages.append(page)
