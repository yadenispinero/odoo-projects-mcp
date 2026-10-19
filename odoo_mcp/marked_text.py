"""Convert marked text from the content generator into blog post HTML.

Marked text is plain text with fixed line markers:

- ``H1: title`` is the post title (at most one; it is not part of the HTML).
- ``H2: `` to ``H6: `` start headings.
- ``• ``, ``- `` or ``* `` start unordered list items; ``1. ``, ``2. `` ... start
  ordered list items.
- Other lines form paragraphs separated by blank lines.

The conversion is a fixed rule: it never removes or adds content. It only
cleans artifacts of the Google Docs text export (trailing spaces, repeated
blank lines, backslash escapes) and produces plain ``p``/``h2``-``h6``/
``ul``/``ol``/``li`` HTML without classes or styles.
"""
from __future__ import annotations

import html
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser

_HEADING = re.compile(r"^H([1-6]):\s*(.*)$")
_BULLET = re.compile(r"^[•\-*]\s+(.*)$")
_NUMBERED = re.compile(r"^\d+\.\s+(.*)$")
_DOCS_ESCAPE = re.compile(r"\\([_#*\[\]])")
_URL = re.compile(r"https?://[^\s<>\"]+")
_URL_TRAILING = ".,;:!?)]}'"
_BLOCK_TAGS = frozenset({"p", "h1", "h2", "h3", "h4", "h5", "h6", "li"})


class MarkedTextError(ValueError):
    """Raised when marked text cannot be converted or paired."""


@dataclass(frozen=True)
class Block:
    """One translatable block: its tag and its inner HTML (an Odoo term)."""

    tag: str
    html: str


@dataclass
class MarkedTextDocument:
    title: str | None
    blocks: list[Block] = field(default_factory=list)

    @property
    def html(self) -> str:
        parts: list[str] = []
        open_list: str | None = None
        for block in self.blocks:
            list_tag = block.tag[3:] if block.tag.startswith("li:") else None
            if list_tag != open_list:
                if open_list:
                    parts.append(f"</{open_list}>")
                if list_tag:
                    parts.append(f"<{list_tag}>")
                open_list = list_tag
            tag = "li" if list_tag else block.tag
            parts.append(f"<{tag}>{block.html}</{tag}>")
        if open_list:
            parts.append(f"</{open_list}>")
        return "\n".join(parts)

    @property
    def terms(self) -> list[str]:
        return [block.html for block in self.blocks]


def _inline_html(text: str) -> str:
    """Escape &, < and >, and turn bare http(s) URLs into links."""
    parts: list[str] = []
    position = 0
    for match in _URL.finditer(text):
        url = match.group(0).rstrip(_URL_TRAILING)
        # Keep a closing parenthesis that belongs to the URL, as in Wikipedia links.
        while url.count("(") > url.count(")") and match.group(0)[len(url):len(url) + 1] == ")":
            url += ")"
        start = match.start()
        end = start + len(url)
        parts.append(html.escape(text[position:start], quote=False))
        parts.append(f'<a href="{html.escape(url, quote=True)}">{html.escape(url, quote=False)}</a>')
        position = end
    parts.append(html.escape(text[position:], quote=False))
    return "".join(parts)


def clean_docs_export(text: str) -> list[str]:
    """Return lines without trailing spaces, Docs escapes, or repeated blank lines."""
    lines: list[str] = []
    for raw_line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = _DOCS_ESCAPE.sub(r"\1", raw_line.rstrip())
        if not line.strip():
            if lines and lines[-1] != "":
                lines.append("")
            continue
        lines.append(line.strip())
    while lines and lines[-1] == "":
        lines.pop()
    return lines


def convert_marked_text(text: str) -> MarkedTextDocument:
    """Convert marked text to blocks; ``document.html`` is the post content."""
    if not isinstance(text, str):
        raise MarkedTextError("marked text must be a string")
    title: str | None = None
    blocks: list[Block] = []
    paragraph: list[str] = []
    # True while lines without a marker continue the last list item.
    continuing_item = False

    def flush_paragraph() -> None:
        if paragraph:
            blocks.append(Block("p", _inline_html(" ".join(paragraph))))
            paragraph.clear()

    for line in clean_docs_export(text):
        if line == "":
            flush_paragraph()
            continuing_item = False
            continue
        heading = _HEADING.match(line)
        bullet = _BULLET.match(line)
        numbered = _NUMBERED.match(line)
        if heading and heading.group(2).strip():
            flush_paragraph()
            continuing_item = False
            level, content = heading.group(1), heading.group(2).strip()
            if level == "1":
                if title is not None:
                    raise MarkedTextError("marked text may contain at most one H1 line")
                title = content
            else:
                blocks.append(Block(f"h{level}", _inline_html(content)))
        elif bullet or numbered:
            flush_paragraph()
            list_tag = "ul" if bullet else "ol"
            content = (bullet or numbered).group(1).strip()
            blocks.append(Block(f"li:{list_tag}", _inline_html(content)))
            continuing_item = True
        elif continuing_item:
            last = blocks[-1]
            blocks[-1] = Block(last.tag, last.html + " " + _inline_html(line))
        else:
            paragraph.append(line)
    flush_paragraph()
    return MarkedTextDocument(title, blocks)


class _BlockTextParser(HTMLParser):
    """Collect the visible text of each block element in document order."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.blocks: list[tuple[str, str]] = []
        self._stack: list[tuple[str, list[str]]] = []
        self._outside: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in _BLOCK_TAGS:
            self._flush_outside()
            if tag == "li" and self._stack:
                self._end_block()
            self._stack.append((tag, []))

    def handle_endtag(self, tag: str) -> None:
        if tag in _BLOCK_TAGS and self._stack:
            self._end_block()
        elif tag in {"ul", "ol"} and self._stack and self._stack[-1][0] == "li":
            self._end_block()

    def handle_data(self, data: str) -> None:
        if self._stack:
            self._stack[-1][1].append(data)
        else:
            self._outside.append(data)

    def close(self) -> None:
        super().close()
        while self._stack:
            self._end_block()
        self._flush_outside()

    def _end_block(self) -> None:
        tag, chunks = self._stack.pop()
        text = normalize_text("".join(chunks))
        if text:
            self.blocks.append((tag, text))

    def _flush_outside(self) -> None:
        text = normalize_text("".join(self._outside))
        self._outside.clear()
        if text:
            self.blocks.append(("text", text))


def normalize_text(value: str) -> str:
    return " ".join(value.split())


def term_text(term: str) -> str:
    """Visible text of an Odoo term (inner HTML of a block)."""
    parser = _BlockTextParser()
    parser.feed(f"<p>{term}</p>")
    parser.close()
    return " ".join(text for _, text in parser.blocks)


def html_blocks(content: str) -> list[tuple[str, str]]:
    """Return ``(tag, visible text)`` for each non-empty block of HTML content."""
    parser = _BlockTextParser()
    parser.feed(content or "")
    parser.close()
    return parser.blocks


def pair_translation_terms(source_content: str, source_terms: list[str],
                           translated: MarkedTextDocument) -> dict[str, str]:
    """Pair translated blocks with the source terms by position.

    ``source_content`` is the post's HTML in the primary language and
    ``source_terms`` are its Odoo terms in order (Odoo lists each distinct
    term once). Raises MarkedTextError when the block count or block types
    differ, or when a repeated source block receives different translations.
    """
    source_blocks = html_blocks(source_content)
    translated_tags = ["li" if block.tag.startswith("li:") else block.tag for block in translated.blocks]
    source_tags = [tag for tag, _ in source_blocks]
    if len(source_blocks) != len(translated.blocks):
        raise MarkedTextError(
            f"content_marked_text has {len(translated.blocks)} blocks but the primary-language "
            f"content has {len(source_blocks)}; both texts must have the same structure"
        )
    for position, (source_tag, translated_tag) in enumerate(zip(source_tags, translated_tags), start=1):
        if source_tag != translated_tag:
            raise MarkedTextError(
                f"Block {position} is {translated_tag} in content_marked_text but {source_tag} in the "
                "primary-language content; both texts must have the same structure"
            )

    pairs: dict[str, str] = {}
    by_text: dict[str, str] = {}
    remaining = list(source_terms)
    for position, ((_, text), block) in enumerate(zip(source_blocks, translated.blocks), start=1):
        if text in by_text:
            term = by_text[text]
            if pairs[term] != block.html:
                raise MarkedTextError(
                    f"Block {position} repeats an earlier primary-language block but has a different "
                    "translation; Odoo stores one translation per repeated term"
                )
            continue
        if not remaining or normalize_text(term_text(remaining[0])) != text:
            raise MarkedTextError(
                f"Block {position} of the primary-language content does not match its Odoo "
                "translation terms; the post content has elements this format cannot pair"
            )
        term = remaining.pop(0)
        by_text[text] = term
        pairs[term] = block.html
    if remaining:
        raise MarkedTextError(
            "The primary-language content has translation terms outside its text blocks; "
            "use content_terms for this post"
        )
    return pairs
