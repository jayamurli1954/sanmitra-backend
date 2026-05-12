import argparse
import asyncio
import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4


sys.path.append(os.getcwd())

from app.db.mongo import close_mongo, get_collection, init_mongo
from app.modules.rag.providers import get_embedding_provider, get_embedding_strategy_name
from app.modules.rag.service import _chunk_text, _tokenize, ensure_rag_indexes


TENANT_ID = "seed-tenant-1"
APP_KEY = "legalmitra"
CREATED_BY = "manual-structured-statute-ingest"
PDF_DIR = Path("data/legal_acts")
LEGAL_STATUTE_SECTIONS_COLLECTION = "legal_statute_sections"
RAG_DOCUMENTS_COLLECTION = "rag_documents"
RAG_CHUNKS_COLLECTION = "rag_chunks"

_SECTION_HEADING_RE = re.compile(
    r"(?m)^\s*(?:\d+\[)?(?P<section>\d+[A-Z]?)\.\s+(?P<title>[^\n]{1,260})"
)
_SPLIT_SECTION_HEADING_RE = re.compile(
    r"(?m)^(\s*(?:\d+\[)?\d+[A-Z]?\.)\s*\n\s*([^\n]{1,260}(?:—|–|―|\.-|-))"
)
_SECTION_ONE_RE = re.compile(r"(?m)^\s*(?:\d+\[)?1\.\s+")
_TOC_MARKERS = ("ARRANGEMENT OF SECTIONS",)


@dataclass(frozen=True)
class ActManifestEntry:
    key: str
    title: str
    pdf_filename: str
    external_id: str
    doc_date: str
    effective_date: str | None


ACTS: tuple[ActManifestEntry, ...] = (
    ActManifestEntry("constitution", "Constitution of India", "constitution_of_india.pdf", "official-constitution-of-india", "1949-11-26", "1950-01-26"),
    ActManifestEntry("bns", "Bharatiya Nyaya Sanhita, 2023", "bns_2023.pdf", "official-bharatiya-nyaya-sanhita-2023", "2023-12-25", "2024-07-01"),
    ActManifestEntry("bnss", "Bharatiya Nagarik Suraksha Sanhita, 2023", "bnss_2023.pdf", "official-bharatiya-nagarik-suraksha-sanhita-2023", "2023-12-25", "2024-07-01"),
    ActManifestEntry("bsa", "Bharatiya Sakshya Adhiniyam, 2023", "bsa_2023.pdf", "official-bharatiya-sakshya-adhiniyam-2023", "2023-12-25", "2024-07-01"),
    ActManifestEntry("cpc", "Code of Civil Procedure, 1908", "cpc_1908.pdf", "official-code-of-civil-procedure-1908", "1908-03-21", "1909-01-01"),
    ActManifestEntry("contract", "Indian Contract Act, 1872", "contract_act_1872.pdf", "official-indian-contract-act-1872", "1872-04-18", "1872-09-01"),
    ActManifestEntry("evidence", "Indian Evidence Act, 1872", "indian_evidence_act_1872.pdf", "official-indian-evidence-act-1872", "1872-07-17", "1872-09-01"),
    ActManifestEntry("companies", "Companies Act, 2013", "companies_act_2013.pdf", "official-companies-act-2013", "2013-03-29", "2013-04-01"),
    ActManifestEntry("llp", "The Limited Liability Partnership Act, 2008", "limited_liability_partnership_act_2008.pdf", "official-limited-liability-partnership-act-2008", "2009-01-07", "2009-03-31"),
    ActManifestEntry("negotiable", "Negotiable Instruments Act, 1881", "negotiable_instruments_act_1881.pdf", "official-negotiable-instruments-act-1881", "1881-12-09", "1882-03-01"),
    ActManifestEntry("ipc", "Indian Penal Code, 1860", "ipc_1860.pdf", "official-indian-penal-code-1860", "1860-10-06", "1862-01-01"),
    ActManifestEntry("crpc", "Code of Criminal Procedure, 1973", "crpc_1973.pdf", "official-code-of-criminal-procedure-1973", "1973-12-25", "1974-04-01"),
    ActManifestEntry("transfer", "Transfer of Property Act, 1882", "transfer_of_property_act_1882.pdf", "official-transfer-of-property-act-1882", "1882-12-09", "1883-07-01"),
    ActManifestEntry("succession", "Indian Succession Act, 1925", "indian_succession_act_1925.pdf", "official-indian-succession-act-1925", "1925-01-20", "1925-07-01"),
    ActManifestEntry("registration", "Registration Act, 1908", "registration_act_1908.pdf", "official-registration-act-1908", "1908-03-21", "1909-01-01"),
    ActManifestEntry("limitation", "Limitation Act, 1963", "limitation_act_1963.pdf", "official-limitation-act-1963", "1963-12-25", "1964-01-01"),
    ActManifestEntry("relief", "Specific Relief Act, 1963", "specific_relief_act_1963.pdf", "official-specific-relief-act-1963", "1963-12-25", "1964-01-01"),
    ActManifestEntry("motor_vehicles", "Motor Vehicles Act, 1988", "motor_vehicles_act_1988.pdf", "official-motor-vehicles-act-1988", "1988-12-30", "1989-06-12"),
    ActManifestEntry("hindu_marriage", "Hindu Marriage Act, 1955", "hindu_marriage_act_1955.pdf", "official-hindu-marriage-act-1955", "1955-05-18", "1956-01-01"),
    ActManifestEntry("information_technology", "Information Technology Act, 2000", "information_technology_act_2000.pdf", "official-information-technology-act-2000", "2000-10-09", "2000-10-17"),
    ActManifestEntry("consumer_protection", "Consumer Protection Act, 2019", "consumer_protection_act_2019.pdf", "official-consumer-protection-act-2019", "2019-08-08", "2020-07-20"),
    ActManifestEntry("income_tax_amended", "Income Tax Act, 2025 (as amended by FA Act 2026)", "income_tax_act_2025_amended.pdf", "official-income-tax-act-2025-amended", "2025-02-01", "2026-04-01"),
    ActManifestEntry("cgst", "Central Goods and Services Tax Act, 2017", "cgst_act_2017.pdf", "official-cgst-act-2017", "2017-03-29", "2017-07-01"),
    ActManifestEntry("arbitration", "Arbitration and Conciliation Act, 1996", "arbitration_and_conciliation_act_1996.pdf", "official-arbitration-and-conciliation-act-1996", "1996-01-25", "1996-07-16"),
    ActManifestEntry("copyright", "Copyright Act, 1957", "copyright_act_1957.pdf", "official-copyright-act-1957", "1957-04-30", "1958-01-21"),
    ActManifestEntry("insolvency", "Insolvency and Bankruptcy Code, 2016", "insolvency_and_bankruptcy_code_2016.pdf", "official-insolvency-and-bankruptcy-code-2016", "2016-05-28", "2016-12-28"),
    ActManifestEntry("patents", "Patents Act, 1970", "patents_act_1970.pdf", "official-patents-act-1970", "1970-09-19", "1972-04-20"),
    ActManifestEntry("special_marriage", "Special Marriage Act, 1954", "special_marriage_act_1954.pdf", "official-special-marriage-act-1954", "1954-10-25", "1955-06-01"),
    ActManifestEntry("environment", "Environment Protection Act, 1986", "environment_protection_act_1986.pdf", "official-environment-protection-act-1986", "1986-05-23", "1986-11-19"),
    ActManifestEntry("industrial_disputes", "Industrial Disputes Act, 1947", "industrial_disputes_act_1947.pdf", "official-industrial-disputes-act-1947", "1947-03-01", "1947-08-01"),
)


@dataclass(frozen=True)
class ParsedSection:
    section: str
    title: str
    text: str
    order: int


def _extract_pdf_text(path: Path) -> str:
    try:
        import fitz  # PyMuPDF
    except ModuleNotFoundError as exc:
        raise RuntimeError("PyMuPDF is required for PDF ingestion. Install the `pymupdf` package.") from exc

    with fitz.open(path) as pdf:
        return "\n".join(page.get_text("text") for page in pdf).strip()


def _strip_table_of_contents(text: str) -> str:
    upper = text[:50000].upper()
    marker_positions = [upper.find(marker) for marker in _TOC_MARKERS if marker in upper]
    if not marker_positions:
        return text

    marker_at = min(marker_positions)
    starts_after_marker = [match for match in _SECTION_ONE_RE.finditer(text) if match.start() > marker_at]
    if len(starts_after_marker) >= 2:
        return text[starts_after_marker[1].start() :].strip()

    return text


def _clean_title(raw_title: str) -> str:
    title = " ".join((raw_title or "").split())
    title = re.split(r"—|–|―|\.-|-", title, maxsplit=1)[0].strip()
    title = re.sub(r"^\[|\]$", "", title).strip()
    title = re.sub(r"\s+", " ", title)
    return title[:240] or "Untitled section"


def _section_sort_key(section: str) -> tuple[int, str]:
    match = re.match(r"^(\d+)([A-Z]?)$", section)
    if not match:
        return (10**9, section)
    return (int(match.group(1)), match.group(2))


def parse_statute_sections(text: str) -> list[ParsedSection]:
    body = _strip_table_of_contents(text)
    body = _SPLIT_SECTION_HEADING_RE.sub(r"\1 \2", body)
    matches = list(_SECTION_HEADING_RE.finditer(body))
    if not matches:
        return []

    sections: list[ParsedSection] = []
    seen: set[str] = set()
    last_sort_key: tuple[int, str] | None = None

    for index, match in enumerate(matches):
        section = match.group("section").strip().upper()
        raw_title = match.group("title")
        raw_title_lower = raw_title.lower()
        if (
            "—" not in raw_title
            and "–" not in raw_title
            and "―" not in raw_title
            and ".-" not in raw_title
            and "[omitted" not in raw_title_lower
            and "[repealed" not in raw_title_lower
        ):
            continue

        sort_key = _section_sort_key(section)
        if section in seen:
            continue

        if last_sort_key is not None and sort_key[0] > last_sort_key[0] + 15:
            continue

        # After the table of contents is removed, actual sections should move
        # broadly forward. This avoids accidental matches from page headers.
        if last_sort_key is not None and sort_key < last_sort_key:
            continue

        next_start = matches[index + 1].start() if index + 1 < len(matches) else len(body)
        section_text = body[match.start() : next_start].strip()
        section_text = re.sub(r"\n{3,}", "\n\n", section_text)
        section_text = re.sub(r"[ \t]+", " ", section_text)

        if len(section_text) < 10:
            continue

        title = _clean_title(raw_title)
        sections.append(
            ParsedSection(
                section=section,
                title=title,
                text=section_text,
                order=len(sections) + 1,
            )
        )
        seen.add(section)
        last_sort_key = sort_key

    return sections


async def ensure_structured_statute_indexes() -> None:
    sections = get_collection(LEGAL_STATUTE_SECTIONS_COLLECTION)
    await sections.create_index([("tenant_id", 1), ("app_key", 1), ("act_key", 1), ("section", 1)], unique=True)
    await sections.create_index([("tenant_id", 1), ("app_key", 1), ("act_name", 1), ("section", 1)])
    await sections.create_index([("tenant_id", 1), ("app_key", 1), ("keywords", 1)])
    await sections.create_index([("tenant_id", 1), ("app_key", 1), ("updated_at", -1)])


def _build_section_record(
    *,
    entry: ActManifestEntry,
    parsed: ParsedSection,
    pdf_path: Path,
    now: datetime,
) -> dict[str, Any]:
    tokens = _tokenize(f"{entry.title} section {parsed.section} {parsed.title} {parsed.text}")
    keywords = sorted({token for token in tokens if len(token) >= 4})[:200]
    return {
        "tenant_id": TENANT_ID,
        "app_key": APP_KEY,
        "act_key": entry.key,
        "act_name": entry.title,
        "section": parsed.section.lower(),
        "section_display": parsed.section,
        "section_title": parsed.title,
        "text": parsed.text,
        "order": parsed.order,
        "jurisdiction": "India",
        "matter_type": "statute",
        "source_file": str(pdf_path),
        "source_uri": pdf_path.as_uri(),
        "external_id": f"{entry.external_id}:section:{parsed.section.lower()}",
        "doc_version": "official",
        "doc_date": entry.doc_date,
        "effective_date": entry.effective_date,
        "keywords": keywords,
        "updated_at": now,
    }


async def _upsert_rag_section(
    *,
    entry: ActManifestEntry,
    parsed: ParsedSection,
    section_record: dict[str, Any],
    chunk_size: int,
    chunk_overlap: int,
    embed: bool,
    now: datetime,
) -> tuple[str, int, str]:
    documents = get_collection(RAG_DOCUMENTS_COLLECTION)
    chunks_collection = get_collection(RAG_CHUNKS_COLLECTION)

    external_id = section_record["external_id"]
    existing = await documents.find_one(
        {
            "tenant_id": TENANT_ID,
            "app_key": APP_KEY,
            "external_id": external_id,
        }
    )
    document_id = str(existing.get("document_id")) if existing else str(uuid4())

    parts = _chunk_text(parsed.text, chunk_size, chunk_overlap)
    if not parts:
        return document_id, 0, "skipped"

    title = f"{entry.title} - Section {parsed.section}: {parsed.title}"
    indexed_parts = [f"{title}\n{part}" for part in parts]

    provider_name = "none"
    model_name = "none"
    strategy_name = "structured_statute_no_embedding_v1"
    embeddings: list[list[float]] = [[] for _ in indexed_parts]

    if embed:
        provider = get_embedding_provider()
        provider_name = provider.name
        model_name = provider.model_name
        strategy_name = get_embedding_strategy_name()
        embeddings = await provider.embed_texts(indexed_parts)

    legal_metadata = {
        "jurisdiction": "India",
        "act_name": entry.title,
        "section": parsed.section.lower(),
        "matter_type": "statute",
        "doc_date": entry.doc_date,
    }
    filter_fields = {
        "legal_jurisdiction": "india",
        "legal_act_name": entry.title.lower(),
        "legal_section": parsed.section.lower(),
        "legal_matter_type": "statute",
        "legal_doc_date": entry.doc_date,
    }
    await chunks_collection.delete_many(
        {
            "tenant_id": TENANT_ID,
            "app_key": APP_KEY,
            "document_id": document_id,
        }
    )

    chunk_docs: list[dict[str, Any]] = []
    for idx, part in enumerate(indexed_parts):
        tokens = _tokenize(part)
        token_set = sorted(set(tokens))
        chunk_doc = {
            "chunk_id": str(uuid4()),
            "document_id": document_id,
            "tenant_id": TENANT_ID,
            "app_key": APP_KEY,
            "title": title,
            "source_type": "statute",
            "source_uri": section_record["source_uri"],
            "language": "en",
            "tags": ["india", "official", "statute", entry.key, f"section-{parsed.section.lower()}"],
            "metadata": {
                "source_file": section_record["source_file"],
                "ingest_manifest_key": entry.key,
                "structured_statute": True,
                "section_title": parsed.title,
            },
            "legal_metadata": legal_metadata,
            "doc_version": "official",
            "effective_date": entry.effective_date,
            "chunk_index": idx,
            "text": part,
            "token_count": len(tokens),
            "token_set": token_set,
            "embedding": embeddings[idx] if idx < len(embeddings) else [],
            "embedding_provider": provider_name,
            "embedding_model": model_name,
            "embedding_strategy": strategy_name,
            "created_at": now,
        }
        chunk_doc.update(filter_fields)
        chunk_docs.append(chunk_doc)

    document_doc = {
        "document_id": document_id,
        "tenant_id": TENANT_ID,
        "app_key": APP_KEY,
        "title": title,
        "source_type": "statute",
        "source_uri": section_record["source_uri"],
        "language": "en",
        "tags": ["india", "official", "statute", entry.key, f"section-{parsed.section.lower()}"],
        "external_id": external_id,
        "doc_version": "official",
        "effective_date": entry.effective_date,
        "metadata": {
            "source_file": section_record["source_file"],
            "ingest_manifest_key": entry.key,
            "structured_statute": True,
            "section_title": parsed.title,
        },
        "legal_metadata": legal_metadata,
        "embedding_provider": provider_name,
        "embedding_model": model_name,
        "embedding_strategy": strategy_name,
        "chunk_count": len(chunk_docs),
        "created_by": CREATED_BY,
        "created_at": existing.get("created_at", now) if existing else now,
        "updated_at": now,
    }
    document_doc.update(filter_fields)

    await documents.update_one(
        {
            "tenant_id": TENANT_ID,
            "app_key": APP_KEY,
            "external_id": external_id,
        },
        {"$set": document_doc},
        upsert=True,
    )
    if chunk_docs:
        await chunks_collection.insert_many(chunk_docs)

    return document_id, len(chunk_docs), "updated" if existing else "inserted"


async def ingest_entry(
    *,
    entry: ActManifestEntry,
    pdf_dir: Path,
    chunk_size: int,
    chunk_overlap: int,
    embed: bool,
    dry_run: bool,
    limit_sections: int | None,
) -> dict[str, Any]:
    pdf_path = (pdf_dir / entry.pdf_filename).resolve()
    if not pdf_path.exists():
        return {"key": entry.key, "status": "missing_pdf", "sections": 0}

    text = _extract_pdf_text(pdf_path)
    parsed_sections = parse_statute_sections(text)
    if limit_sections:
        parsed_sections = parsed_sections[:limit_sections]

    if dry_run:
        return {
            "key": entry.key,
            "status": "dry_run",
            "sections": len(parsed_sections),
            "first_sections": [f"{item.section}: {item.title}" for item in parsed_sections[:5]],
        }

    section_collection = get_collection(LEGAL_STATUTE_SECTIONS_COLLECTION)
    now = datetime.now(timezone.utc)
    inserted = 0
    updated = 0
    chunk_count = 0

    for parsed in parsed_sections:
        section_record = _build_section_record(entry=entry, parsed=parsed, pdf_path=pdf_path, now=now)
        previous = await section_collection.find_one(
            {
                "tenant_id": TENANT_ID,
                "app_key": APP_KEY,
                "act_key": entry.key,
                "section": parsed.section.lower(),
            }
        )
        await section_collection.update_one(
            {
                "tenant_id": TENANT_ID,
                "app_key": APP_KEY,
                "act_key": entry.key,
                "section": parsed.section.lower(),
            },
            {"$set": section_record, "$setOnInsert": {"created_at": now}},
            upsert=True,
        )
        _, chunks, status = await _upsert_rag_section(
            entry=entry,
            parsed=parsed,
            section_record=section_record,
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            embed=embed,
            now=now,
        )
        chunk_count += chunks
        if previous or status == "updated":
            updated += 1
        else:
            inserted += 1

    return {
        "key": entry.key,
        "status": "ok",
        "sections": len(parsed_sections),
        "inserted": inserted,
        "updated": updated,
        "chunks": chunk_count,
    }


def _selected_entries(args: argparse.Namespace) -> list[ActManifestEntry]:
    by_key = {entry.key: entry for entry in ACTS}
    selected_keys = args.only or list(by_key)
    unknown = sorted(set(selected_keys) - set(by_key))
    if unknown:
        raise SystemExit(f"Unknown act key(s): {', '.join(unknown)}")
    return [by_key[key] for key in selected_keys]


async def main() -> None:
    parser = argparse.ArgumentParser(
        description="Ingest official Indian bare-act PDFs as section-level LegalMitra RAG records."
    )
    parser.add_argument("--pdf-dir", default=str(PDF_DIR), help="Directory containing official PDFs.")
    parser.add_argument("--only", nargs="+", choices=[entry.key for entry in ACTS], help="Only ingest selected act keys.")
    parser.add_argument("--chunk-size", type=int, default=3500, help="RAG chunk size for long sections.")
    parser.add_argument("--chunk-overlap", type=int, default=120, help="RAG chunk overlap for long sections.")
    parser.add_argument("--embed", action="store_true", help="Generate embeddings with the configured provider.")
    parser.add_argument("--dry-run", action="store_true", help="Parse PDFs and report section counts without DB writes.")
    parser.add_argument("--limit-sections", type=int, default=None, help="Limit sections per act for testing.")
    args = parser.parse_args()

    if args.chunk_overlap >= args.chunk_size:
        raise SystemExit("--chunk-overlap must be less than --chunk-size")

    entries = _selected_entries(args)
    if not entries:
        print("Nothing to ingest.")
        return

    if args.dry_run:
        for entry in entries:
            result = await ingest_entry(
                entry=entry,
                pdf_dir=Path(args.pdf_dir),
                chunk_size=args.chunk_size,
                chunk_overlap=args.chunk_overlap,
                embed=args.embed,
                dry_run=True,
                limit_sections=args.limit_sections,
            )
            print(result)
        return

    await init_mongo()
    try:
        await ensure_rag_indexes()
        await ensure_structured_statute_indexes()
        for entry in entries:
            result = await ingest_entry(
                entry=entry,
                pdf_dir=Path(args.pdf_dir),
                chunk_size=args.chunk_size,
                chunk_overlap=args.chunk_overlap,
                embed=args.embed,
                dry_run=False,
                limit_sections=args.limit_sections,
            )
            print(result)
    finally:
        await close_mongo()


if __name__ == "__main__":
    asyncio.run(main())
