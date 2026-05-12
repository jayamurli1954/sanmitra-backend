from scripts.ingest_structured_statutes import parse_statute_sections


def test_parse_statute_sections_skips_arrangement_of_sections() -> None:
    text = """
THE SAMPLE ACT
ARRANGEMENT OF SECTIONS
1. Short title.
2. Definitions.

1. Short title.—This Act may be called the Sample Act.
2. Definitions.—In this Act, unless the context otherwise requires, "person" includes a company.
3. Application.—This Act applies to India.
"""

    sections = parse_statute_sections(text)

    assert [section.section for section in sections] == ["1", "2", "3"]
    assert sections[0].title == "Short title."
    assert "Sample Act" in sections[0].text
    assert "ARRANGEMENT OF SECTIONS" not in sections[0].text


def test_parse_statute_sections_supports_lettered_sections_and_footnote_prefix() -> None:
    text = """
1. Title.—Body text.
2. Definitions.—Definition text.
9[3A. Electronic record.—Lettered section text.
4. Saving.—Saving text.
"""

    sections = parse_statute_sections(text)

    assert [section.section for section in sections] == ["1", "2", "3A", "4"]
    assert sections[2].title == "Electronic record."


def test_parse_statute_sections_supports_split_heading_lines() -> None:
    text = """
1.
Short title, extent and commencement.― (1) This Act may be called the Sample Act.
2.
Definitions.— In this Act, "record" means a document.
"""

    sections = parse_statute_sections(text)

    assert [section.section for section in sections] == ["1", "2"]
    assert sections[0].title == "Short title, extent and commencement."
