import pytest

from odoo_mcp.marked_text import MarkedTextError, convert_marked_text, pair_translation_terms


def test_converts_headings_lists_and_paragraphs():
    document = convert_marked_text(
        "H1: Post title\n\nIntro line one\nline two.\n\nH2: Section\n\n"
        "• first\n- second\n* third\n\n1. one\n2. two\n\nH3: Detail\n\nClosing."
    )
    assert document.title == "Post title"
    assert document.html == (
        "<p>Intro line one line two.</p>\n<h2>Section</h2>\n"
        "<ul>\n<li>first</li>\n<li>second</li>\n<li>third</li>\n</ul>\n"
        "<ol>\n<li>one</li>\n<li>two</li>\n</ol>\n<h3>Detail</h3>\n<p>Closing.</p>"
    )


def test_cleans_real_google_docs_export():
    document = convert_marked_text(
        "H2: Título\n\n  \n\n• punto uno.  \n\n• punto dos.\n\n\n\n"
        "Más en https://apps.odoo.com/apps/modules/19.0/xopgi\\_sam."
    )
    assert document.title is None
    assert document.html == (
        "<h2>Título</h2>\n<ul>\n<li>punto uno.</li>\n<li>punto dos.</li>\n</ul>\n"
        '<p>Más en <a href="https://apps.odoo.com/apps/modules/19.0/xopgi_sam">'
        "https://apps.odoo.com/apps/modules/19.0/xopgi_sam</a>.</p>"
    )


def test_ignores_byte_order_mark_from_docs_export():
    document = convert_marked_text("\ufeffH1: Título\r\n\r\n\r\nH2: Intro")
    assert document.title == "Título"
    assert document.html == "<h2>Intro</h2>"


def test_unescapes_docs_escapes_and_escapes_html():
    document = convert_marked_text("\\#Odoo \\*bold\\* \\[x\\] a & b <tag> 5 > 3")
    assert document.html == "<p>#Odoo *bold* [x] a &amp; b &lt;tag&gt; 5 &gt; 3</p>"


def test_keeps_content_such_as_hashtags_and_calls_to_action():
    document = convert_marked_text("Escríbenos hoy.\n\n#OdooBerlin #ERP")
    assert document.html == "<p>Escríbenos hoy.</p>\n<p>#OdooBerlin #ERP</p>"


def test_link_escapes_ampersand_and_keeps_balanced_parenthesis():
    document = convert_marked_text(
        "See https://example.test/a?x=1&y=2, or (https://en.wikipedia.org/wiki/Odoo_(software))."
    )
    assert document.html == (
        '<p>See <a href="https://example.test/a?x=1&amp;y=2">https://example.test/a?x=1&amp;y=2</a>, '
        'or (<a href="https://en.wikipedia.org/wiki/Odoo_(software)">'
        "https://en.wikipedia.org/wiki/Odoo_(software)</a>).</p>"
    )


def test_rejects_more_than_one_h1():
    with pytest.raises(MarkedTextError, match="at most one H1"):
        convert_marked_text("H1: One\n\nH1: Two")


def test_list_item_continuation_and_list_type_change():
    document = convert_marked_text("• item\ncontinued\n1. numbered")
    assert document.html == "<ul>\n<li>item continued</li>\n</ul>\n<ol>\n<li>numbered</li>\n</ol>"


SOURCE_HTML = (
    "<h2>Section</h2>\n<p>Read <a href=\"https://example.test\">https://example.test</a> today.</p>\n"
    "<ul>\n<li>first</li>\n<li>second</li>\n</ul>"
)
SOURCE_TERMS = [
    "Section", 'Read <a href="https://example.test">https://example.test</a> today.', "first", "second",
]


def test_pairs_translation_terms_by_position():
    translated = convert_marked_text(
        "H1: Título\n\nH2: Sección\n\nLee https://example.test hoy.\n\n• primero\n• segundo"
    )
    assert pair_translation_terms(SOURCE_HTML, SOURCE_TERMS, translated) == {
        "Section": "Sección",
        'Read <a href="https://example.test">https://example.test</a> today.':
            'Lee <a href="https://example.test">https://example.test</a> hoy.',
        "first": "primero",
        "second": "segundo",
    }


def test_pairing_rejects_different_block_count():
    translated = convert_marked_text("H2: Sección\n\nLee hoy.\n\n• primero")
    with pytest.raises(MarkedTextError, match="3 blocks .* 4"):
        pair_translation_terms(SOURCE_HTML, SOURCE_TERMS, translated)


def test_pairing_rejects_different_block_type():
    translated = convert_marked_text("H3: Sección\n\nLee hoy.\n\n• primero\n• segundo")
    with pytest.raises(MarkedTextError, match="Block 1 is h3 .* but h2"):
        pair_translation_terms(SOURCE_HTML, SOURCE_TERMS, translated)


def test_pairing_handles_repeated_source_blocks_once():
    source = "<p>Book now.</p>\n<p>Middle.</p>\n<p>Book now.</p>"
    translated = convert_marked_text("Reserva ya.\n\nMedio.\n\nReserva ya.")
    assert pair_translation_terms(source, ["Book now.", "Middle."], translated) == {
        "Book now.": "Reserva ya.", "Middle.": "Medio.",
    }
    different = convert_marked_text("Reserva ya.\n\nMedio.\n\nReserva hoy.")
    with pytest.raises(MarkedTextError, match="different translation"):
        pair_translation_terms(source, ["Book now.", "Middle."], different)


def test_pairing_rejects_terms_outside_text_blocks():
    translated = convert_marked_text("Hola.")
    with pytest.raises(MarkedTextError, match="outside its text blocks"):
        pair_translation_terms("<p>Hello.</p><img alt=\"Logo\">", ["Hello.", "Logo"], translated)
