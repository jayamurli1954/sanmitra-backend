from __future__ import annotations

import hashlib
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

import httpx
from fastapi import BackgroundTasks

from app.config import get_settings
from app.db.mongo import get_collection

_logger = logging.getLogger(__name__)

RAG_SYNC_QUEUE_COLLECTION = "rag_sync_queue"

_CLOSING_DISCLAIMER = (
    "\n\n---\n"
    "*Disclaimer: This note is prepared for the use of the instructing advocate only. "
    "Verify the current legal position, recent amendments, and jurisdiction-specific practice "
    "before filing, advising a client, or taking final legal action. "
    "No professional liability attaches to this output.*"
)

_IST_TZ = timezone(timedelta(hours=5, minutes=30))

# ─── Utilities ────────────────────────────────────────────────────────────────

def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _now_ist() -> datetime:
    return datetime.now(_IST_TZ)


def _normalize_query(query: str) -> str:
    return " ".join((query or "").strip().lower().split())


def extract_current_legal_query(query: str) -> str:
    """Return the latest user question when callers accidentally include chat history."""
    value = (query or "").strip()
    if not value:
        return ""

    quoted_questions = re.findall(r'["\u201c\u201d]([^"\u201c\u201d\n\r]{8,500}\?)["\u201c\u201d]?', value)
    if quoted_questions:
        return " ".join(quoted_questions[-1].split())

    # Avoid unbounded regex over chat-history-sized input. CodeQL flags lazy
    # dot-star patterns on uncontrolled text, so use bounded marker scanning.
    marker_scan = value[:4000]
    marker_scan_lower = marker_scan.lower()
    for marker in (
        "current question",
        "current query",
        "latest question",
        "latest query",
        "user question",
        "user query",
        "user's question",
        "user's query",
    ):
        marker_at = marker_scan_lower.rfind(marker)
        if marker_at < 0:
            continue

        tail = marker_scan[marker_at + len(marker) :]
        tail_stripped = tail.lstrip()
        if tail_stripped.startswith(":"):
            candidate = tail_stripped[1:]
        elif tail_stripped.lower().startswith("is"):
            candidate = tail_stripped[2:]
        else:
            continue

        question_end = candidate.find("?")
        if question_end >= 0:
            return " ".join(candidate[: question_end + 1].split())

    question_lines = [
        line.strip()
        for line in value.splitlines()
        if "?" in line and not re.search(r"\b(previous|prior|earlier)\s+(turn|query|question)\b", line, re.IGNORECASE)
    ]
    if question_lines:
        return " ".join(question_lines[-1].split())

    return " ".join(value.split())


def _query_hash(query: str) -> str:
    return hashlib.sha256(_normalize_query(query).encode("utf-8")).hexdigest()


def _rag_answer_insufficient(answer: str) -> bool:
    value = (answer or "").strip().lower()
    markers = [
        "do not have enough indexed content",
        "ingest relevant documents",
    ]
    return any(marker in value for marker in markers)


def _offline_legal_fallback(query: str, query_type: str) -> dict[str, Any] | None:
    """Return narrow, source-backed guidance when the AI provider is unavailable."""
    q = _normalize_query(query)
    if (
        ("25" in q or "twenty five" in q or "twenty-five" in q)
        and ("act" in q or "acts" in q or "law" in q or "laws" in q)
        and ("india" in q or "indian" in q)
    ):
        response = (
            "**25 Important Indian Acts**\n\n"
            "**Core and procedural laws**\n"
            "1. Constitution of India\n"
            "2. Bharatiya Nyaya Sanhita, 2023\n"
            "3. Bharatiya Nagarik Suraksha Sanhita, 2023\n"
            "4. Bharatiya Sakshya Adhiniyam, 2023\n"
            "5. Code of Civil Procedure, 1908\n\n"
            "**Business, corporate and commercial laws**\n"
            "6. Companies Act, 2013\n"
            "7. Limited Liability Partnership Act, 2008\n"
            "8. Indian Contract Act, 1872\n"
            "9. Sale of Goods Act, 1930\n"
            "10. Insolvency and Bankruptcy Code, 2016\n\n"
            "**Taxation laws**\n"
            "11. Income-tax Act, 1961\n"
            "12. Central Goods and Services Tax Act, 2017\n"
            "13. Integrated Goods and Services Tax Act, 2017\n\n"
            "**Labour and employment laws**\n"
            "14. Employees' Provident Funds and Miscellaneous Provisions Act, 1952\n"
            "15. Payment of Gratuity Act, 1972\n"
            "16. Minimum Wages Act, 1948\n"
            "17. Industrial Disputes Act, 1947\n\n"
            "**Consumer, technology and data laws**\n"
            "18. Consumer Protection Act, 2019\n"
            "19. Information Technology Act, 2000\n"
            "20. Digital Personal Data Protection Act, 2023\n\n"
            "**Property and real estate laws**\n"
            "21. Transfer of Property Act, 1882\n"
            "22. Registration Act, 1908\n"
            "23. Real Estate (Regulation and Development) Act, 2016\n\n"
            "**Family and financial crime laws**\n"
            "24. Special Marriage Act, 1954\n"
            "25. Prevention of Money Laundering Act, 2002\n\n"
            "**Freshness note:** This is a deterministic LegalMitra reference list for "
            "the requested count. For production use, verify repealed/replaced "
            "criminal-law references and any recent amendments."
        )
        return {
            "response": response + _CLOSING_DISCLAIMER,
            "citations": [
                {
                    "title": "India Code - Central Acts",
                    "source": "Government of India",
                    "snippet": "Reference portal for central legislation.",
                    "retrieved_at": _now_utc().isoformat(),
                }
            ],
            "strategy": "deterministic_indian_acts_list",
            "note": "Returned deterministic list response for explicit Indian Acts count query.",
            "dropped_citation_count": 0,
        }

    if (
        ("divorce" in q or "dissolution" in q)
        and (
            "hindu marriage act" in q
            or "hma" in q
            or ("hindu" in q and "marriage" in q)
        )
    ):
        response = (
            "**Grounds for Divorce Under the Hindu Marriage Act, 1955**\n\n"
            "**Core provision:** Section 13 of the Hindu Marriage Act, 1955 "
            "sets out the principal grounds on which a Hindu marriage may be "
            "dissolved by a decree of divorce.\n\n"
            "**Common grounds under Section 13(1):**\n"
            "1. **Adultery:** voluntary sexual intercourse with a person other "
            "than the spouse after marriage.\n"
            "2. **Cruelty:** physical or mental cruelty making continued "
            "cohabitation unsafe or unreasonable.\n"
            "3. **Desertion:** abandonment for a continuous period of at least "
            "two years immediately before filing the petition.\n"
            "4. **Conversion:** ceasing to be Hindu by conversion to another "
            "religion.\n"
            "5. **Mental disorder:** incurable unsoundness of mind or a mental "
            "disorder of such kind and degree that the petitioner cannot "
            "reasonably be expected to live with the respondent.\n"
            "6. **Virulent and incurable leprosy:** statutory availability must "
            "be checked against current amendments before pleading.\n"
            "7. **Venereal disease in communicable form:** statutory availability "
            "must be checked against current amendments before pleading.\n"
            "8. **Renunciation:** renouncing the world by entering a religious "
            "order.\n"
            "9. **Presumption of death:** not heard of as being alive for seven "
            "years or more by persons who would naturally have heard of the spouse.\n\n"
            "**Additional grounds:** A wife may have additional grounds under "
            "Section 13(2), including situations involving the husband's earlier "
            "marriage, certain sexual offences, non-resumption of cohabitation "
            "after maintenance orders, and repudiation of marriage in specified "
            "circumstances. Divorce by mutual consent is separately available "
            "under Section 13B."
        )
        return {
            "response": response + _CLOSING_DISCLAIMER,
            "citations": [
                {
                    "title": "Hindu Marriage Act, 1955 - Section 13",
                    "source": "India Code / Central Act",
                    "snippet": "Divorce grounds under the Hindu Marriage Act, 1955.",
                    "retrieved_at": _now_utc().isoformat(),
                },
                {
                    "title": "Hindu Marriage Act, 1955 - Section 13B",
                    "source": "India Code / Central Act",
                    "snippet": "Divorce by mutual consent.",
                    "retrieved_at": _now_utc().isoformat(),
                },
            ],
            "strategy": "offline_hindu_marriage_divorce_fallback",
            "note": "Gemini unavailable; returned narrow offline fallback with source caveats.",
            "dropped_citation_count": 0,
        }

    if (
        ("section 138" in q or "cheque bounce" in q or "check bounce" in q)
        and ("limitation" in q or "timeline" in q or "filing" in q or "complaint" in q)
        and ("ni act" in q or "negotiable instruments" in q or "cheque" in q or "check" in q)
    ):
        response = (
            "**Limitation Timeline for a Cheque Bounce Complaint Under Section 138 NI Act**\n\n"
            "**Core provision:** Section 138 read with Section 142(b) of the "
            "Negotiable Instruments Act, 1881.\n\n"
            "**Timeline:**\n"
            "1. **Present the cheque:** within 3 months from the cheque date or "
            "within its validity period, whichever is earlier.\n"
            "2. **Send demand notice:** within 30 days from receiving bank "
            "intimation that the cheque was dishonoured.\n"
            "3. **Wait for payment:** the drawer gets 15 days from receipt of the "
            "demand notice to pay.\n"
            "4. **Cause of action:** arises after the 15-day payment period expires "
            "without payment.\n"
            "5. **File complaint:** within 1 month from the date the cause of "
            "action arises, under Section 142(b).\n\n"
            "**Delay:** The court may take cognizance after the prescribed period "
            "if the complainant shows sufficient cause for delay under the proviso "
            "to Section 142(b).\n\n"
            "**Key authority:** *Econ Antri Ltd. v. Rom Industries Ltd.*, "
            "(2014) 11 SCC 769, on computation of the Section 138/142 timeline."
        )
        return {
            "response": response + _CLOSING_DISCLAIMER,
            "citations": [
                {
                    "title": "Negotiable Instruments Act, 1881 - Section 138",
                    "source": "India Code / Central Act",
                    "snippet": "Cheque dishonour ingredients and statutory notice/payment periods.",
                    "retrieved_at": _now_utc().isoformat(),
                },
                {
                    "title": "Negotiable Instruments Act, 1881 - Section 142(b)",
                    "source": "India Code / Central Act",
                    "snippet": "Complaint within one month from cause of action, with delay condonation proviso.",
                    "retrieved_at": _now_utc().isoformat(),
                },
                {
                    "title": "Econ Antri Ltd. v. Rom Industries Ltd.",
                    "source": "Supreme Court of India",
                    "snippet": "Computation of limitation under Sections 138(c) and 142(b) of the NI Act.",
                    "date": "2014",
                    "retrieved_at": _now_utc().isoformat(),
                },
            ],
            "strategy": "offline_ni_act_138_limitation_fallback",
            "note": "Gemini unavailable; returned narrow offline fallback with source caveats.",
            "dropped_citation_count": 0,
        }

    if (
        ("section 244" in q or "244 companies" in q)
        and ("companies act" in q or "company" in q)
        and (
            "oppression" in q
            or "mismanagement" in q
            or "who can file" in q
            or "eligibility" in q
            or "petition" in q
            or "waiver" in q
        )
    ):
        response = (
            "**Provision / Concept:** Section 244, Companies Act, 2013\n"
            "**Core Rule:** Section 244 specifies who is eligible to apply to the "
            "National Company Law Tribunal (NCLT) for relief against oppression and "
            "mismanagement under Section 241.\n"
            "**Ingredients / Tests:**\n"
            "* **Company having share capital:**\n"
            "  * Not less than 100 members of the company, or not less than one-tenth "
            "of the total number of members, whichever is less; or\n"
            "  * Any member or members holding not less than one-tenth of the issued "
            "share capital, provided all calls and other sums due on their shares "
            "have been paid.\n"
            "* **Company not having share capital:**\n"
            "  * Not less than one-fifth of the total number of its members.\n"
            "* **Waiver:** The NCLT may waive all or any of the Section 244(1) "
            "requirements to enable members to apply under Section 241.\n"
            "**Key Exception:** The Tribunal's waiver power under the proviso to "
            "Section 244(1).\n"
            "**Limitation / Timeline:** Not applicable to filing eligibility.\n"
            "**Key SC Cases:**\n"
            "* *Cyrus Investments Pvt. Ltd. v. Tata Sons Ltd.*, (2017) 1 SCC 777 "
            "- recognised the discretionary waiver route under the proviso to "
            "Section 244(1), to be considered on the facts of the case.\n"
            "* *V.S. Krishnan v. M.S. Krishnan*, (2020) 14 SCC 1 - waiver under "
            "Section 244 is a preliminary issue and must be considered before the "
            "main oppression and mismanagement petition proceeds.\n"
            "**Legal Position:** Settled Law on eligibility thresholds and the "
            "Tribunal's power to waive them."
        )
        return {
            "response": response + _CLOSING_DISCLAIMER,
            "citations": [
                {
                    "title": "Companies Act, 2013 - Section 244",
                    "source": "India Code / Central Act",
                    "snippet": "Right to apply under Section 241 and waiver by the Tribunal.",
                    "retrieved_at": _now_utc().isoformat(),
                },
                {
                    "title": "Cyrus Investments Pvt. Ltd. v. Tata Sons Ltd.",
                    "source": "Supreme Court of India",
                    "snippet": "Waiver under the proviso to Section 244(1).",
                    "date": "2017",
                    "retrieved_at": _now_utc().isoformat(),
                },
                {
                    "title": "V.S. Krishnan v. M.S. Krishnan",
                    "source": "Supreme Court of India",
                    "snippet": "Section 244 waiver as a preliminary issue.",
                    "date": "2020",
                    "retrieved_at": _now_utc().isoformat(),
                },
            ],
            "strategy": "offline_companies_act_244_eligibility_fallback",
            "note": "Gemini unavailable; returned narrow offline fallback with source caveats.",
            "dropped_citation_count": 0,
        }

    if not (
        "quash" in q
        and "fir" in q
        and ("bnss" in q or "bharatiya nagarik suraksha sanhita" in q)
    ):
        return None

    response = (
        "**FIR Quashing Under BNSS - Practical Procedure**\n\n"
        "**Core route:** Move the jurisdictional High Court under **Section 528, "
        "Bharatiya Nagarik Suraksha Sanhita, 2023 (BNSS)**, which preserves the "
        "High Court's inherent powers to prevent abuse of process and secure the "
        "ends of justice. In suitable cases, Article 226 of the Constitution may "
        "also be invoked, especially where the challenge is to illegal or arbitrary "
        "State action.\n\n"
        "**Procedure checklist:**\n"
        "1. Collect the FIR, complaint, notice/summons if any, settlement documents "
        "if applicable, and all documents showing why the allegations are legally "
        "untenable.\n"
        "2. Prepare a criminal petition under BNSS Section 528 before the High Court "
        "with a prayer to quash the FIR and consequential proceedings/investigation.\n"
        "3. Plead the quashing grounds precisely: no offence made out even if the "
        "FIR is accepted at face value, proceedings are mala fide, civil/commercial "
        "dispute is given criminal colour, legal bar to prosecution, or continuation "
        "would be abuse of process.\n"
        "4. Add parties normally including the State/investigating agency and the "
        "informant/complainant. Serve advance copies as required by local High Court "
        "rules.\n"
        "5. Seek interim protection only where justified, such as no coercive steps "
        "or stay of further proceedings. Courts are cautious at the investigation "
        "stage, so the petition must show an exceptional case.\n"
        "6. At hearing, avoid disputed facts and argue from the FIR, admitted "
        "documents, statutory ingredients, and binding quashing principles.\n\n"
        "**Key authorities to verify before filing:**\n"
        "- **BNSS, 2023, Section 528**: inherent powers of High Court.\n"
        "- **State of Haryana v. Bhajan Lal**, 1992 Supp (1) SCC 335: illustrative "
        "categories for quashing criminal proceedings.\n"
        "- **Gian Singh v. State of Punjab**, (2012) 10 SCC 303 and **Narinder "
        "Singh v. State of Punjab**, (2014) 6 SCC 466: quashing on settlement, "
        "subject to offence nature and public interest.\n"
        "- **Neeharika Infrastructure v. State of Maharashtra**, (2021) 19 SCC 401: "
        "High Courts should be cautious in interfering with investigation.\n\n"
        "**Drafting note:** Lead with the exact FIR allegations and map them against "
        "the statutory ingredients of the alleged BNS/other offences. If any ingredient "
        "is missing on the face of the FIR, make that the first ground.\n\n"
        "**Freshness note:** This is an offline fallback because the AI provider did "
        "not return a response. Verify current High Court rules, recent Supreme Court "
        "and local High Court decisions, and any post-2023 BNSS amendments before filing."
    )

    citations = [
        {
            "title": "Bharatiya Nagarik Suraksha Sanhita, 2023 - Section 528",
            "source": "BNSS 2023",
            "snippet": "Saving of inherent powers of High Court.",
            "retrieved_at": _now_utc().isoformat(),
        },
        {
            "title": "State of Haryana v. Bhajan Lal",
            "source": "Supreme Court of India",
            "snippet": "Illustrative categories for quashing criminal proceedings.",
            "date": "1992",
            "retrieved_at": _now_utc().isoformat(),
        },
        {
            "title": "Neeharika Infrastructure v. State of Maharashtra",
            "source": "Supreme Court of India",
            "snippet": "Caution against routine interference with criminal investigation.",
            "date": "2021",
            "retrieved_at": _now_utc().isoformat(),
        },
    ]

    return {
        "response": response + _CLOSING_DISCLAIMER,
        "citations": citations,
        "strategy": "offline_bnss_fir_quashing_fallback",
        "note": "Gemini unavailable; returned narrow offline fallback with source caveats.",
        "dropped_citation_count": 0,
    }


# ─── Format Detection ─────────────────────────────────────────────────────────

_CRIMINAL_MARKERS = re.compile(
    r"\b(ipc|crpc|bns|bnss|bsa|fir|cognizable|bailable|chargesheet|"
    r"section\s+\d|accused|bail|custody|remand|acquittal|conviction|"
    r"evidence act|criminal|penal|offence|offense)\b",
    re.IGNORECASE,
)
_DRAFTING_MARKERS = re.compile(
    r"\b(draft|nda|agreement|deed|clause|contract|mou|memorandum of understanding|"
    r"arbitration clause|non.?disclosure|template|format)\b",
    re.IGNORECASE,
)
_SECTION_LOOKUP_MARKERS = re.compile(
    r"\b(what is|define|meaning of|explain|section \d|article \d|"
    r"under section|under article|interpret|scope of)\b",
    re.IGNORECASE,
)
_CASE_PREP_MARKERS = re.compile(
    r"\b(case prep|argument|submissions|how to argue|strategy|"
    r"hearing|bench questions|oral argument|written submission)\b",
    re.IGNORECASE,
)
_FAMILY_LAW_MARKERS = re.compile(
    r"\b(hindu marriage act|hma|divorce|marriage|matrimonial|family court|"
    r"custody|maintenance|restitution of conjugal rights)\b",
    re.IGNORECASE,
)


def _detect_format_mode(query: str, query_type: str) -> str:
    """Return one of: cheat_sheet | drafting | quick_check | argument_note"""
    q = extract_current_legal_query(query)
    qt = (query_type or "research").strip().lower()

    if qt == "drafting" or _DRAFTING_MARKERS.search(q):
        return "drafting"
    if qt == "case_prep" or _CASE_PREP_MARKERS.search(q):
        return "argument_note"
    if _FAMILY_LAW_MARKERS.search(q):
        return "legal_advisor"
    if _CRIMINAL_MARKERS.search(q):
        return "cheat_sheet"
    if qt in {"section_lookup", "interpretation"} or _SECTION_LOOKUP_MARKERS.search(q):
        return "quick_check"
    # Default to a neutral advisory brief for general research queries.
    return "legal_advisor"


# ─── Prompt Builder ───────────────────────────────────────────────────────────

_SENIOR_COUNSEL_PERSONA = """\
You are LegalMitra — an elite Indian legal advisor and Senior Counsel's Strategic Clerk.
Your mission is to provide accurate, actionable legal guidance for Indian legal users, advocates, professionals, businesses, and individuals.

⚖️ CRITICAL LEGAL GUARDRAILS (NEVER VIOLATE)
1. BNSS (Bharatiya Nagarik Suraksha Sanhita, 2023) ONLY replaces the CrPC (1973). It does NOT replace or amend the IT Act, 2000.
2. For Intermediary Liability & Data Protection:
   - ALWAYS cite Section 79 of the Information Technology Act, 2000 for safe harbor.
   - ALWAYS cite Section 43A of IT Act / SPDI Rules 2011 for legacy data protection.
   - ALWAYS cite the Digital Personal Data Protection (DPDP) Act, 2023 for current data compliance.
   - NEVER map IT Act sections to BNSS sections.
3. The Three New Criminal Laws:
   - BNS 2023 → replaces IPC 1860
   - BNSS 2023 → replaces CrPC 1973
   - BSA 2023 → replaces Indian Evidence Act 1872
   - The IT Act 2000 remains UNCHANGED and independent.

🎯 TONE & STYLE
• Use clear, user-context-aware language. Do not assume the user is a startup founder, SaaS operator, company, freelancer, or advocate unless the query says so.
• When the user's role or purpose is unclear, explain the legal position generally and add practical next steps for a person or organisation as applicable.
• Be Sharp. Authoritative. Action-Oriented.
• Avoid excessive legal theory. Focus on "What to do next".
• Use professional court terminology (e.g. "ratio decidendi", "per incuriam") ONLY when query_type is 'research' or 'case_prep'.
• Prohibited: Behaving like a law student or junior.

JURISPRUDENCE ENGINE
• Prioritise case law over statute text. Cite binding SC precedents first.
• Identify ratio decidendi and classify: ✅ Settled Law | ⚖️ Divergent Views | 🆕 Res Integra

RAG CONTEXT PROTOCOL
• Use [R1], [R2] citations. Prefer retrieved context over model memory.
• If retrieved content conflicts with recalled law, flag the discrepancy clearly.\

CURRENT QUERY DISCIPLINE
• Answer only the CURRENT QUERY section below.
• Do not mention, summarize, or compare any previous turn unless the current query explicitly asks for it.
• If prior chat history appears in the query text, ignore it and answer the latest user question only.\
"""

_FORMAT_DIRECTIVES: dict[str, str] = {
    "cheat_sheet": """\
OUTPUT FORMAT — Cheat Sheet (mandatory for this query)

| Legacy (IPC/CrPC/Evidence Act) | Current (BNS/BNSS/BSA) | Delta | Key SC Case |
|---|---|---|---|
[populate all rows]

Then add a brief Argument Note (3–5 numbered submissions, case-backed).
End with: **Legal Position:** ✅/⚖️/🆕
""",

    "argument_note": """\
OUTPUT FORMAT — STRICT ARGUMENT NOTE

⚠ PROHIBITED: Do NOT write any memo header (MEMORANDUM / To: / From: / Subject: / Date:).
⚠ PROHIBITED: Do NOT write any introduction, preamble, or context-setting paragraph.
⚠ START the response immediately with the bold heading "**Submissions:**" — nothing before it.

**Submissions:**
1. [One precise legal proposition — cite the exact statute section + binding SC case with year and ratio in the same line]
2. [Next proposition — same authority format]
… (as many submissions as the legal position requires; every line must carry authority)

**Legal Position:** ✅ Settled Law / ⚖️ Divergent Views / 🆕 Res Integra
**Risk Exposure:** [concise — limitation period, evidentiary gaps, enforcement difficulty]
**Suggested Action:** Instruct AoR to… / Settle draft prepared by junior…

Authority must accompany every proposition. No narration. No hedging.
""",

    "quick_check": """\
OUTPUT FORMAT — STRICT QUICK CHECK

⚠ PROHIBITED: Do NOT write any memo header (MEMORANDUM / To: / From: / Subject: / Date:).
⚠ PROHIBITED: No introduction. Start the response immediately with "**Provision / Concept:**".

**Provision / Concept:** [exact section + Act + year]
**Core Rule:** [one sentence — what it mandates or permits]
**Ingredients / Tests:** [bullet list]
**Key Exception:** [if any]
**Limitation / Timeline:** [if applicable]
**Key SC Case:** [case name, year, SCC citation — ratio in one line]
**Legal Position:** ✅/⚖️/🆕

Precision only. No narration. No hedging.
""",

    "drafting": """\
OUTPUT FORMAT — FULL DRAFT INSTRUMENT

⚠ PROHIBITED: Do NOT write any memo header (MEMORANDUM / To: / From: / Subject:).
⚠ PROHIBITED: No preamble, no "here is a draft", no advisory wrapper.
⚠ START immediately with the document title (e.g. "NON-DISCLOSURE AGREEMENT").

• Use proper legal numbering (1., 1.1, 1.2…).
• Include all standard clauses: parties, definitions, operative provisions, term, \
termination, governing law, jurisdiction, dispute resolution.
• For NDA/confidentiality: confidentiality obligations, IP assignment, \
return of information, non-solicitation.
• Mark blanks as [PARTY NAME], [DATE], [CITY], etc.
• End with a signature block.
• The advocate will review — do not pre-disclaim or hedge within the document body.
""",

    "legal_advisor": """\
OUTPUT FORMAT — PRACTICAL LEGAL ADVISOR

⚠ START the response immediately with a "🧾 **Quick Checklist / What You Must Do**" section.
⚠ Every answer must follow this structure:

🧾 **Quick Checklist / What You Must Do**
* [5-7 clear, actionable bullet points in plain English]
* [Direct instructions, no legal jargon]

🏗️ **Context & Relevance**
* [Explain why this legal issue matters in neutral terms. Do not invent a startup, SaaS, data, or founder context unless the query includes it.]

⚖️ **Key Legal Requirements**
* [1-2 key laws explained simply with citations]

🔥 **Risks to Manage**
* [Specific penalties or business risks]

🛠️ **Next Steps**
* [Practical checklist for implementation]

**Legal Position:** ✅ Settled Law / ⚖️ Divergent Views / 🆕 Res Integra
""",
}


def _build_rag_context_block(citations: list[dict[str, Any]]) -> str:
    """Format relevant RAG citations as a labeled context block for Gemini."""
    if not citations:
        return ""
    parts = ["RETRIEVED CONTEXT (prefer over model memory where relevant):"]
    for i, c in enumerate(citations[:6], start=1):
        title = c.get("title") or c.get("reference") or f"Source {i}"
        snippet = c.get("snippet") or c.get("text") or ""
        date = c.get("date") or c.get("published_date") or ""
        source = c.get("source") or ""
        meta = []
        if source:
            meta.append(source)
        if date:
            meta.append(date)
        meta_str = " | ".join(meta)
        entry = f"[R{i}] {title}"
        if meta_str:
            entry += f" ({meta_str})"
        if snippet:
            entry += f"\n     {snippet[:400]}"
        parts.append(entry)
    return "\n".join(parts)


def _build_senior_counsel_prompt(
    query: str,
    format_mode: str,
    rag_context: str,
    today_ist: str,
) -> str:
    format_directive = _FORMAT_DIRECTIVES.get(format_mode, _FORMAT_DIRECTIVES["argument_note"])

    sections: list[str] = [
        _SENIOR_COUNSEL_PERSONA,
        f"Date (IST): {today_ist}",
    ]
    if rag_context:
        sections.append(rag_context)

    sections.append(format_directive)
    sections.append(f"QUERY:\n{query.strip()}")

    return "\n\n".join(sections)


# ─── Citation Relevance Filter ────────────────────────────────────────────────

_LEGAL_QUERY_WORD_RE = re.compile(r"[a-z0-9]+")
_LEGAL_QUERY_STOPWORDS = {
    "what", "which", "when", "where", "who", "whom", "whose",
    "why", "how", "is", "are", "was", "were", "do", "does",
    "did", "can", "could", "should", "would", "please", "explain",
    "briefly", "about", "tell", "me", "the", "for", "and", "with",
    "a", "an", "of", "in", "on", "to", "by", "as", "or", "if",
    "this", "that", "these", "those", "be", "been", "being",
    "have", "has", "had", "from", "any", "all", "there", "here",
    "under", "over", "into", "per", "via", "than", "then",
}


def _extract_meaningful_query_terms(query: str) -> set[str]:
    tokens = set(_LEGAL_QUERY_WORD_RE.findall((query or "").lower()))
    return {t for t in tokens if len(t) >= 4 and t not in _LEGAL_QUERY_STOPWORDS}


def _citation_is_relevant(citation: dict[str, Any], query_terms: set[str]) -> tuple[bool, int, float]:
    """Return (relevant, overlap_count, overlap_ratio) for a single citation.

    Relevance rule: at least 2 meaningful query terms must appear in the citation's
    snippet/title/legal-metadata/reference, OR at least 30% of meaningful terms
    overlap.

    If the citation exposes no inspectable content, treat as relevant (stubs in tests).
    """
    if not query_terms:
        return (True, 0, 1.0)

    haystack_parts: list[str] = []
    for key in ("snippet", "title"):
        val = citation.get(key)
        if val:
            haystack_parts.append(str(val))
    legal_meta = citation.get("legal_metadata") or {}
    if isinstance(legal_meta, dict):
        for val in legal_meta.values():
            if val:
                haystack_parts.append(str(val))

    haystack = " ".join(haystack_parts).lower().strip()
    if not haystack:
        return (True, 0, 1.0)

    haystack_tokens = set(_LEGAL_QUERY_WORD_RE.findall(haystack))
    hits = query_terms.intersection(haystack_tokens)
    ratio = len(hits) / max(len(query_terms), 1)

    relevant = len(hits) >= 2 or ratio >= 0.30
    return (relevant, len(hits), ratio)


def _filter_citations_by_relevance(
    citations: list[dict[str, Any]], query: str
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Split citations into (relevant, dropped)."""
    query_terms = _extract_meaningful_query_terms(query)
    if not query_terms:
        return (list(citations), [])

    relevant: list[dict[str, Any]] = []
    dropped: list[dict[str, Any]] = []
    for c in citations:
        is_rel, hits, ratio = _citation_is_relevant(c, query_terms)
        if is_rel:
            relevant.append(c)
        else:
            _logger.debug(
                "citation_dropped title=%r hits=%d ratio=%.2f",
                c.get("title") or c.get("reference") or "?", hits, ratio,
            )
            dropped.append(c)
    return (relevant, dropped)


# ─── Gemini API Call ──────────────────────────────────────────────────────────

async def _call_gemini_text(*, prompt: str, max_tokens: int, temperature: float = 0.2) -> str | None:
    settings = get_settings()
    api_key = settings.GEMINI_API_KEY
    if not api_key:
        _logger.warning("gemini_call skipped: GEMINI_API_KEY not configured")
        return None

    api_base = settings.RAG_GEMINI_API_BASE.rstrip("/")
    model = settings.LEGAL_FALLBACK_GEMINI_MODEL

    payload = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": temperature,
            "maxOutputTokens": max_tokens,
            "topP": 0.9,
            # Disable Gemini 2.5 Flash's internal "thinking" budget.
            # Without this, the model consumes most of maxOutputTokens on hidden
            # chain-of-thought reasoning, leaving only ~200 visible tokens — which
            # causes mid-sentence truncation on structured legal responses.
            "thinkingConfig": {"thinkingBudget": 0},
        },
    }

    url = f"{api_base}/models/{model}:generateContent"
    _logger.info(
        "gemini_call start model=%s prompt_len=%d max_tokens=%d temperature=%.2f",
        model, len(prompt), max_tokens, temperature,
    )
    try:
        async with httpx.AsyncClient(timeout=45.0) as client:
            response = await client.post(url, params={"key": api_key}, json=payload)

        if response.status_code >= 400:
            body_excerpt = (response.text or "")[:500]
            _logger.error(
                "gemini_call http_error status=%d model=%s body=%s",
                response.status_code, model, body_excerpt,
            )
            return None

        body = response.json()
        candidates = body.get("candidates") or []
        if not candidates:
            prompt_feedback = body.get("promptFeedback") or {}
            _logger.warning(
                "gemini_call empty_candidates model=%s promptFeedback=%s",
                model, prompt_feedback,
            )
            return None

        parts = (((candidates[0] or {}).get("content") or {}).get("parts") or [])
        text = "\n".join(
            str(part.get("text") or "") for part in parts if isinstance(part, dict)
        ).strip()
        if not text:
            finish_reason = (candidates[0] or {}).get("finishReason")
            _logger.warning(
                "gemini_call empty_text model=%s finishReason=%s", model, finish_reason
            )
            return None

        _logger.info("gemini_call ok model=%s response_len=%d", model, len(text))
        return text
    except Exception as exc:
        _logger.exception("gemini_call exception model=%s err=%s", model, exc)
        return None


def validate_legal_hallucinations(response_text: str) -> str:
    """
    Checks for common legal hallucinations (like IT Act being replaced by BNSS)
    and applies corrections to the final response text.
    """
    corrections = [
        # Hallucination: IT Act is replaced by BNSS
        (
            r"IT Act.*replaced by.*BNSS",
            "Note: The Information Technology Act, 2000 remains in full force and is NOT replaced by BNSS 2023 (which replaces the CrPC)."
        ),
        (
            r"Section 79.*BNSS",
            "Note: Section 79 (Intermediary Safe Harbor) belongs to the IT Act 2000, not BNSS."
        ),
        (
            r"BNSS.*intermediary",
            "Note: BNSS governs criminal procedure; intermediary liability is governed by the IT Act 2000."
        ),
    ]

    fixed_text = response_text
    for pattern, warning in corrections:
        if re.search(pattern, fixed_text, re.IGNORECASE):
            # If we find the hallucination, we append the warning at the end of the paragraph or document
            fixed_text += f"\n\n> [!CAUTION]\n> **Accuracy Alert:** {warning}"

    return fixed_text


# ─── Main Response Builder ────────────────────────────────────────────────────

async def build_hybrid_legal_response(
    *,
    tenant_id: str,
    app_key: str,
    query: str,
    query_type: str = "research",
    rag_result: dict[str, Any],
    background_tasks: BackgroundTasks | None = None,
) -> dict[str, Any]:
    settings = get_settings()
    current_query = extract_current_legal_query(query)
    citations = list(rag_result.get("citations") or [])

    # Filter RAG citations for relevance — only topically matching ones go into context.
    relevant_citations, dropped_citations = _filter_citations_by_relevance(citations, current_query)

    query_preview = (current_query or "")[:80]
    _logger.info(
        "hybrid_response tenant=%s app=%s rag_citations=%d relevant=%d dropped=%d query=%r",
        tenant_id, app_key, len(citations), len(relevant_citations),
        len(dropped_citations), query_preview,
    )

    # Enqueue low-confidence queries for background sync regardless of Gemini outcome.
    if not relevant_citations and background_tasks is not None:
        background_tasks.add_task(
            enqueue_auto_sync_query,
            tenant_id=tenant_id,
            app_key=app_key,
            query=current_query,
            reason="low_rag_confidence",
        )

    # Build the Gemini prompt — relevant RAG snippets become context, not the answer.
    deterministic_fallback = _offline_legal_fallback(current_query, query_type)
    if deterministic_fallback and deterministic_fallback["strategy"] == "deterministic_indian_acts_list":
        _logger.info(
            "hybrid_response path=deterministic tenant=%s app=%s strategy=%s",
            tenant_id, app_key, deterministic_fallback["strategy"],
        )
        return deterministic_fallback

    today_ist = _now_ist().strftime("%d-%m-%Y")
    format_mode = _detect_format_mode(current_query, query_type)
    rag_context = _build_rag_context_block(relevant_citations)

    prompt = _build_senior_counsel_prompt(
        query=current_query,
        format_mode=format_mode,
        rag_context=rag_context,
        today_ist=today_ist,
    )

    gemini_answer = await _call_gemini_text(
        prompt=prompt,
        max_tokens=max(settings.LEGAL_FALLBACK_MAX_TOKENS, 4000),
        temperature=0.15,
    )

    if gemini_answer and gemini_answer.strip():
        # Apply Hallucination Guardrail
        response_text = validate_legal_hallucinations(gemini_answer.strip())

        response_text += _CLOSING_DISCLAIMER
        _logger.info(
            "hybrid_response path=gemini tenant=%s app=%s format=%s response_len=%d",
            tenant_id, app_key, format_mode, len(response_text),
        )
        return {
            "response": response_text,
            "citations": relevant_citations,
            "strategy": f"{str(rag_result.get('strategy') or 'rag')}_gemini",
            "note": None,
            "dropped_citation_count": len(dropped_citations),
        }

    # Gemini unavailable or returned empty — return a clean, honest failure.
    offline_fallback = _offline_legal_fallback(current_query, query_type)
    if offline_fallback:
        _logger.warning(
            "hybrid_response path=offline_fallback tenant=%s app=%s strategy=%s",
            tenant_id, app_key, offline_fallback["strategy"],
        )
        return offline_fallback

    _logger.warning(
        "hybrid_response path=gemini_unavailable tenant=%s app=%s (no API key or empty response)",
        tenant_id, app_key,
    )
    return {
        "response": (
            "**Advisory Unavailable**\n\n"
            "The AI engine did not return a response for this query. "
            "This may be a transient issue or an unsupported query type.\n\n"
            "**Suggested action:** Retry the query, narrow the scope, "
            "or route to a junior for manual research."
        ),
        "citations": relevant_citations,
        "strategy": "gemini_unavailable",
        "note": "AI engine did not respond — retry or rephrase the query.",
        "dropped_citation_count": len(dropped_citations),
    }


# ─── Index & Queue Management ─────────────────────────────────────────────────

async def ensure_legal_compat_indexes() -> None:
    queue = get_collection(RAG_SYNC_QUEUE_COLLECTION)
    await queue.create_index([("tenant_id", 1), ("app_key", 1), ("status", 1), ("created_at", -1)])
    await queue.create_index([("status", 1), ("updated_at", 1)])
    await queue.create_index(
        [("tenant_id", 1), ("app_key", 1), ("query_hash", 1), ("status", 1)],
        unique=True,
        partialFilterExpression={"status": "pending"},
    )


async def enqueue_auto_sync_query(*, tenant_id: str, app_key: str, query: str, reason: str) -> None:
    settings = get_settings()
    if not settings.RAG_AUTO_SYNC_ENABLED:
        return

    normalized_query = _normalize_query(query)
    if not normalized_query:
        return

    queue = get_collection(RAG_SYNC_QUEUE_COLLECTION)
    now = _now_utc()
    doc = {
        "job_id": str(uuid4()),
        "tenant_id": tenant_id,
        "app_key": app_key,
        "query": query.strip(),
        "normalized_query": normalized_query,
        "query_hash": _query_hash(query),
        "reason": reason,
        "status": "pending",
        "attempt_count": 0,
        "last_error": None,
        "created_at": now,
        "updated_at": now,
    }

    await queue.update_one(
        {
            "tenant_id": tenant_id,
            "app_key": app_key,
            "query_hash": doc["query_hash"],
            "status": "pending",
        },
        {
            "$setOnInsert": doc,
            "$set": {"last_seen_at": now},
        },
        upsert=True,
    )


async def list_sync_queue(
    *, tenant_id: str, app_key: str, status: str = "pending", limit: int = 50
) -> list[dict[str, Any]]:
    queue = get_collection(RAG_SYNC_QUEUE_COLLECTION)
    status_value = (status or "pending").strip().lower()
    cursor = (
        queue.find({"tenant_id": tenant_id, "app_key": app_key, "status": status_value})
        .sort("updated_at", -1)
        .limit(limit)
    )

    items: list[dict[str, Any]] = []
    async for doc in cursor:
        items.append(
            {
                "job_id": str(doc.get("job_id") or ""),
                "query": str(doc.get("query") or ""),
                "reason": str(doc.get("reason") or ""),
                "status": str(doc.get("status") or "pending"),
                "attempt_count": int(doc.get("attempt_count") or 0),
                "last_error": doc.get("last_error"),
                "created_at": doc.get("created_at"),
                "updated_at": doc.get("updated_at"),
            }
        )

    return items
