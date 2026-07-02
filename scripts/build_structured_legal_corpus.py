"""بناء طبقة بيانات قانونية منظمة من ملفات الأنظمة المرجعية."""

from __future__ import annotations

import json
import csv
import html
import os
import re
import shutil
import subprocess
import tempfile
import unicodedata
import urllib.parse
from collections import Counter
from pathlib import Path

try:
    from pypdf import PdfReader
except ImportError:
    PdfReader = None

try:
    from docx import Document as DocxDocument
except ImportError:
    DocxDocument = None

ROOT = Path(__file__).resolve().parent.parent
CATALOG_PATH = ROOT / "catalog" / "saudi_regulations_catalog.json"
CUSTOM_CATALOG_PATH = ROOT / "catalog" / "custom_regulations_catalog.json"
OFFICIAL_DIR = ROOT / "documents" / "saudi_regulations" / "official_latest"
ONBOARDED_DIR = ROOT / "documents" / "saudi_regulations" / "onboarded"
KNOWLEDGE_DIR = ROOT / "documents" / "knowledge" / "saudi_regulations"
STRUCTURED_DIR = ROOT / "data" / "structured"
BY_REGULATION_DIR = STRUCTURED_DIR / "by_regulation"
OFFICIAL_SNAPSHOTS_DIR = STRUCTURED_DIR / "official_snapshots"
VERBATIM_DIR = STRUCTURED_DIR / "verbatim_texts"
TESSDATA_DIR = ROOT / "data" / "tessdata"
REGULATIONS_JSON = STRUCTURED_DIR / "regulations.json"
REGULATIONS_CSV = STRUCTURED_DIR / "regulations.csv"
ARTICLES_JSONL = STRUCTURED_DIR / "articles.jsonl"
ARTICLES_CSV = STRUCTURED_DIR / "articles.csv"
PARAGRAPHS_JSONL = STRUCTURED_DIR / "paragraphs.jsonl"
PARAGRAPHS_CSV = STRUCTURED_DIR / "paragraphs.csv"
CHUNKS_JSONL = STRUCTURED_DIR / "chunks.jsonl"
CHUNKS_CSV = STRUCTURED_DIR / "chunks.csv"
REPORT_JSON = STRUCTURED_DIR / "build_report.json"

ARTICLE_RE = re.compile(r"^(?:#+\s*)?(?:المادة|املادة|اﻟﻤﺎدة|مادة)\s+(.+?)(?:\s*[:：.]?\s*)(.*)$")
INLINE_ARTICLE_RE = re.compile(r"(?:^|[:：])\s*(?:المادة|املادة|اﻟﻤﺎدة|مادة)\s+(.+?)(?:\s*[:：.]?\s*)(.*)$")
CONTROL_CHAR_PATTERN = re.compile(r"[\u200e\u200f\u202a-\u202e\u2066-\u2069\ufeff]")
RESULT_ITEM_RE = re.compile(
    r'<a class="result-keyword-title" href="(?P<law_href>[^"]+)">(?P<title>[^<]+)</a>.*?'
    r'<a href="(?P<search_href>[^"]*SearchDetails[^"]*)">المزيد من نتائج البحث</a>',
    re.S,
)
ARTICLE_BLOCK_RE = re.compile(
    r'<div class="article_item[^"]*".*?>\s*<h3 class="center">(?P<title>.*?)</h3>.*?'
    r'<div class="HTMLContainer">(?P<body>.*?)</div>.*?</div>',
    re.S,
)
LAW_TEXTBOX_RE = re.compile(
    r'<div class="system_terms_textbox" id="divLawText">(?P<body>.*?)</div>\s*<div class="d-flex justify-content-center">',
    re.S,
)
SDAIA_KNOWLEDGE_SECTION_RE = re.compile(
    r"<section class=\"knowledge-page\"[^>]*>(?P<body>.*?)</section>",
    re.S,
)
UQN_WIDGET_URL_RE = re.compile(r"widget_url:\s*'(?P<url>https://www\.uqn\.gov\.sa/api/article/\d+/json)'")
UQN_CMS_ARTICLE_ID_RE = re.compile(r'id="cms_article_id"\s+value="(?P<id>\d+)"')
UQN_ARTICLE_CONTENT_RE = re.compile(r'<article[^>]+id="article-content"[^>]*>(?P<body>.*?)</article>', re.S)
UQN_ARTICLE_TITLE_RE = re.compile(r'<h1[^>]*class="article-title"[^>]*>(?P<title>.*?)</h1>', re.S)
UQN_DATE_RE = re.compile(
    r'<div class="date-item">\s*<span>\s*(?P<hijri>[^<\n]+?)\s+الموافق\s+(?P<gregorian>[^<\n]+?)\s*</span>',
    re.S,
)
INVALID_BOE_PAGE_MARKERS = (
    "العنصر المطلوب غير موجود بالنظام",
    "عذراً، لقد حدث خطأ",
    "عذرا، لقد حدث خطأ",
)
LOW_SIGNAL_PARAGRAPH_RE = re.compile(r"^[\W\d٠١٢٣٤٥٦٧٨٩_]+$")
OCR_LANGUAGE_DOWNLOAD_URLS = {
    "ara": "https://github.com/tesseract-ocr/tessdata_best/raw/main/ara.traineddata",
}
COMMON_TESSDATA_DIRS = (
    Path("/tmp/tessdata"),
    Path("/opt/homebrew/share/tessdata"),
    Path("/usr/local/share/tessdata"),
    Path("/usr/share/tessdata"),
    Path("/usr/share/tesseract-ocr/4.00/tessdata"),
    Path("/usr/share/tesseract-ocr/5/tessdata"),
)
ARTICLE_TYPE_HEADING_RULES = [
    ("definition", ["تعريف", "التعريفات"]),
    ("rights", ["الحقوق", "حق", "الولاية", "الحضانة", "الوصاية"]),
    ("violation", ["المخالفات", "التعديات", "الاعتداء"]),
    ("penalty", ["العقوبات", "العقوبة", "الجزاءات"]),
    ("procedure", ["التراخيص", "الترخيص", "الإجراءات", "الاختصاص", "الاختصاصات"]),
    ("condition", ["الشروط", "الالتزامات", "الواجبات"]),
    ("exception", ["الاستثناءات", "القيود", "الاستثناء"]),
]
ARTICLE_TYPE_TEXT_RULES = [
    ("penalty", ["يعاقب", "العقوبة", "غرامة", "السجن", "المعاقبة"]),
    ("violation", ["تعديًا على", "تعديا على", "التعدي", "المخالفات", "مخالفة", "الاعتداء على", "تعد التصرفات"]),
    ("prohibition", ["لا يجوز", "يحظر", "يمنع", "لا يحق", "يمتنع"]),
    ("condition", ["يشترط", "يشترط أن", "يلتزم", "يتعين", "يجب", "يلزم", "على كل صاحب عمل"]),
    ("rights", ["له الحق", "فله", "يحق", "حقوق", "تكون الولاية", "تكون الحضانة", "يتولى", "للمؤلف أو من يفوضه حق", "للمؤلف الحق"]),
    ("procedure", ["يجوز للوزير", "للمحكمة", "تقرر المحكمة", "يتقدم", "يطلب", "إجراء", "دعوى", "ترخيص", "تعيين", "إعذار"]),
    ("liability", ["مسؤول", "مسؤولية", "تعويض", "يضمن", "الضرر", "حارس البناء مسؤول"]),
    ("exception", ["استثناء", "يستثنى", "ما لم", "إلا إذا", "دون إخلال", "ومع ذلك"]),
    ("definition", ["يقصد", "يقصد ب", "ويقصد بها", "ويُقصد بها", "تعني", "الولي هو", "الوصي هو"]),
    ("reference", ["المنصوص عليه في المادة", "وفق الترتيب المنصوص عليه", "بحسب الأحكام المنظمة", "الأحكام النظامية ذات العلاقة"]),
]
TOPIC_TAG_RULES = [
    ("custody", ["الحضانة", "الحاضن", "المحضون"]),
    ("guardianship", ["الولاية", "الولي", "الوصي", "القاصر"]),
    ("travel", ["السفر", "خارج المملكة"]),
    ("maintenance", ["النفقة"]),
    ("company_management", ["المدير", "مجلس الإدارة", "الشركاء", "المساهمين"]),
    ("liability", ["المسؤولية", "مسؤول", "التعويض", "الضرر", "المتضرر"]),
    ("property_neighbors", ["الجار", "الجوار", "الحائط", "الجدار", "البناء", "تهدم"]),
    ("evidence", ["الإثبات", "البينة", "الإقرار", "الشهادة", "اليمين"]),
    ("labor", ["العامل", "العمل", "صاحب العمل", "المنشأة", "الأجر"]),
    ("copyright", ["حقوق المؤلف", "المؤلف", "المصنف", "النشر", "ترخيص", "الحقوق المالية", "شبكات المعلومات"]),
    ("e_commerce", ["التجارة الإلكترونية", "موفر الخدمة", "المستهلك", "متجر إلكتروني", "وسيلة إلكترونية", "الإعلان عنها"]),
    ("personal_data", ["البيانات الشخصية", "صاحب البيانات", "جهة التحكم", "معالجة البيانات", "إتلاف البيانات", "تسويقية"]),
    ("commercial_fraud", ["الغش التجاري", "منتج مغشوش", "خدع", "الخداع", "مواصفات", "صفات جوهرية"]),
    ("product_safety", ["سلامة المنتجات", "حماية المستهلك", "الخطر", "مراقبة الأسواق", "المنتجات المتداولة"]),
    ("cybercrime", ["جرائم المعلوماتية", "الشبكة المعلوماتية", "الدخول غير المشروع", "بيانات بنكية", "الاحتيال"]),
    ("enforcement", ["التنفيذ", "قاضي التنفيذ", "السند التنفيذي", "الحجز التنفيذي", "طالب التنفيذ", "المنفذ ضده"]),
    ("anti_money_laundering", ["مكافحة غسل الأموال", "غسل الأموال", "المؤسسات المالية", "وحدة التحريات المالية", "المتحصلات", "العناية الواجبة"]),
    ("harassment", ["التحرش", "تحرش", "جريمة التحرش", "تحرش لفظي", "تحرش جنسي", "المتحرش"]),
    ("education", ["التعليم", "التعليمي", "التعليمية", "المدرسي", "المدرسية", "الجامعي", "الجامعية", "المناهج"]),
    ("workplace_childcare", ["المربيات", "دار الحضانة", "أطفال العاملات", "اطفال العاملات", "ست سنوات"]),
    ("violations", ["المخالفات", "مخالفة", "التعدي", "تعديًا", "تعديا", "الاعتداء"]),
    ("abuse", ["الإيذاء", "حالة إيذاء", "المبلغ", "البلاغ"]),
    ("procurement_ethics", ["المنافسات", "المشتريات الحكومية", "استغلال الوظيفة", "إفشاء المعلومات"]),
]
REGULATION_DEFAULT_TOPIC_TAGS = {
    "copyright-law": ["copyright"],
    "labor-law": ["labor"],
    "companies-law": ["company_management"],
    "law-of-evidence": ["evidence"],
    "universities-law": ["education"],
    "protection-from-abuse-law": ["abuse"],
    "government-tenders-and-procurement-law": ["procurement_ethics"],
    "e-commerce-law": ["e_commerce"],
    "personal-data-protection-law": ["personal_data"],
    "commercial-fraud-law": ["commercial_fraud"],
    "product-safety-law": ["product_safety"],
    "anti-cybercrime-law": ["cybercrime"],
    "execution-law": ["enforcement"],
    "anti-money-laundering-law": ["anti_money_laundering"],
    "nzam-mkafhh-jrymh-althrsh": ["harassment"],
}
REGULATION_ALLOWED_TOPIC_TAGS = {
    "copyright-law": {"copyright", "education", "violations", "liability"},
    "labor-law": {"labor", "workplace_childcare"},
    "companies-law": {"company_management", "liability", "violations"},
    "law-of-evidence": {"evidence", "guardianship"},
    "universities-law": {"education"},
    "civil-transactions-law": {"liability", "property_neighbors"},
    "government-tenders-and-procurement-law": {"procurement_ethics", "liability", "violations"},
    "protection-from-abuse-law": {"abuse", "liability", "violations"},
    "real-estate-brokerage-law": {"liability", "violations"},
    "communications-and-information-technology-law": {"copyright", "violations"},
    "electronic-transactions-law": {"evidence", "education", "violations"},
    "e-commerce-law": {"e_commerce", "personal_data", "commercial_fraud", "liability", "violations"},
    "personal-data-protection-law": {"personal_data", "rights", "violations"},
    "commercial-fraud-law": {"commercial_fraud", "product_safety", "liability", "violations"},
    "product-safety-law": {"product_safety", "liability", "violations"},
    "anti-cybercrime-law": {"cybercrime", "violations", "liability"},
    "execution-law": {"enforcement", "liability", "violations"},
    "anti-money-laundering-law": {"anti_money_laundering", "liability", "violations"},
    "nzam-mkafhh-jrymh-althrsh": {"harassment", "liability", "violations"},
}
ARTICLE_TYPE_LABELS_AR = {
    "definition": "تعريف",
    "rights": "حق أو اختصاص",
    "condition": "شروط أو التزامات",
    "exception": "استثناء أو قيد",
    "liability": "مسؤولية أو تعويض",
    "prohibition": "منع أو حظر",
    "procedure": "إجراء أو سلطة قضائية",
    "penalty": "عقوبة",
    "violation": "مخالفة أو تعدٍ",
    "reference": "إحالة أو ربط نظامي",
    "general": "حكم عام",
}
LEGAL_FUNCTION_RULES = [
    ("definition", ["تعريف", "يقصد", "يقصد ب", "تعني", "المراد", "المقصود"]),
    ("condition", ["يشترط", "شرط", "شروط", "إذا", "متى", "في حال", "عند", "وفقًا للشروط"]),
    ("obligation", ["يلتزم", "يجب", "على كل", "على موفر الخدمة", "على جهة التحكم", "على صاحب العمل", "يؤدي", "يدفع", "تلتزم", "يتعين", "يلزم"]),
    ("prohibition", ["لا يجوز", "يحظر", "يمنع", "لا يحق", "يمتنع"]),
    ("remedy", ["تعويض", "التعويض", "رد المبلغ", "استرداد", "استرداد ما دفعه", "له استرداد", "فسخ", "فسخ العقد", "إلغاء الطلب", "استرجاع", "يصرف له", "يستحق"]),
    ("procedure", ["يتقدم", "يطلب", "إجراء", "تنفيذ", "إبلاغ", "إفصاح", "إشعار", "شكوى", "التظلم", "دعوى"]),
    ("authority", ["للمحكمة", "للقاضي", "لقاضي التنفيذ", "للوزير", "للجهة", "للسلطة", "تقرر المحكمة", "الجهة المختصة"]),
    ("penalty", ["يعاقب", "العقوبة", "العقوبات", "غرامة", "السجن", "الجزاء"]),
    ("exception", ["استثناء", "استثناءات", "ما لم", "إلا", "ومع ذلك", "دون إخلال", "يستثنى"]),
    ("deadline", ["خلال", "مدة", "يوما", "يوماً", "أيام", "أسبوع", "شهر", "فور", "مهلة", "التأخير"]),
    ("burden_of_proof", ["الإثبات", "يثبت", "عبء الإثبات", "عبء الاثبات", "البينة", "على من يدعي"]),
    ("claimant_right", ["له الحق", "للمستهلك الحق", "للعامل الحق", "لصاحب البيانات الحق", "يحق له", "يحق للمستهلك", "يحق لصاحب البيانات", "فله", "له استرداد"]),
]
ARTICLE_TYPE_DEFAULT_FUNCTIONS = {
    "definition": ["definition"],
    "rights": ["claimant_right"],
    "condition": ["condition", "obligation"],
    "exception": ["exception"],
    "liability": ["remedy"],
    "prohibition": ["prohibition"],
    "procedure": ["procedure", "authority"],
    "penalty": ["penalty"],
    "violation": ["prohibition"],
    "reference": [],
    "general": [],
}
LEGAL_FUNCTION_LABELS_AR = {
    "definition": "تعريف",
    "condition": "شرط",
    "obligation": "التزام",
    "prohibition": "منع",
    "remedy": "علاج أو تعويض",
    "procedure": "إجراء",
    "authority": "اختصاص",
    "penalty": "جزاء",
    "exception": "استثناء",
    "deadline": "مهلة زمنية",
    "burden_of_proof": "عبء إثبات",
    "claimant_right": "حق لصاحب الطلب",
}
TOPIC_TAG_LABELS_AR = {
    "custody": "حضانة",
    "guardianship": "ولاية ووصاية",
    "travel": "سفر",
    "maintenance": "نفقة",
    "company_management": "إدارة الشركات",
    "liability": "مسؤولية وتعويض",
    "property_neighbors": "الجوار والبناء",
    "evidence": "إثبات",
    "labor": "عمل",
    "copyright": "حقوق المؤلف والنشر",
    "e_commerce": "تجارة إلكترونية",
    "personal_data": "بيانات شخصية",
    "commercial_fraud": "غش تجاري",
    "product_safety": "سلامة المنتجات",
    "cybercrime": "جرائم معلوماتية",
    "enforcement": "تنفيذ",
    "anti_money_laundering": "مكافحة غسل الأموال",
    "harassment": "تحرش",
    "education": "تعليم",
    "workplace_childcare": "حضانة العمل",
    "violations": "مخالفات وتعديات",
    "abuse": "إيذاء وحماية",
    "procurement_ethics": "نزاهة المنافسات والمشتريات",
}
VERSION_STATUS_LABELS_AR = {
    "current_official_text": "النص الرسمي الحالي",
    "current_official_text_with_embedded_amendment_notes": "النص الحالي مع إشارات تعديل",
    "current_official_text_recent_update_pending_publication": "تحديث حديث يحتاج متابعة نشره التفصيلي",
}

ORDINAL_UNIT_MAP = {
    "الاول": 1,
    "الاولى": 1,
    "الاولي": 1,
    "الحادي": 1,
    "الحادية": 1,
    "الثاني": 2,
    "الثانية": 2,
    "الثانيه": 2,
    "الثالث": 3,
    "الثالثة": 3,
    "الثالثه": 3,
    "الرابع": 4,
    "الرابعة": 4,
    "الرابعه": 4,
    "الخامس": 5,
    "الخامسة": 5,
    "الخامسه": 5,
    "السادس": 6,
    "السادسة": 6,
    "السادسه": 6,
    "السابع": 7,
    "السابعة": 7,
    "السابعه": 7,
    "الثامن": 8,
    "الثامنة": 8,
    "الثامنه": 8,
    "التاسع": 9,
    "التاسعة": 9,
    "التاسعه": 9,
    "العاشر": 10,
    "العاشرة": 10,
    "العاشره": 10,
}

ORDINAL_TEENS_MAP = {
    "الحادي عشر": 11,
    "الحادية عشرة": 11,
    "الحادي عشرة": 11,
    "الحادي عشره": 11,
    "الحادية عشره": 11,
    "الثاني عشر": 12,
    "الثانية عشرة": 12,
    "الثانية عشره": 12,
    "الثالث عشر": 13,
    "الثالثة عشرة": 13,
    "الثالثة عشره": 13,
    "الرابع عشر": 14,
    "الرابعة عشرة": 14,
    "الرابعة عشره": 14,
    "الخامس عشر": 15,
    "الخامسة عشرة": 15,
    "الخامسة عشره": 15,
    "السادس عشر": 16,
    "السادسة عشرة": 16,
    "السادسة عشره": 16,
    "السابع عشر": 17,
    "السابعة عشرة": 17,
    "السابعة عشره": 17,
    "الثامن عشر": 18,
    "الثامنة عشرة": 18,
    "الثامنة عشره": 18,
    "التاسع عشر": 19,
    "التاسعة عشرة": 19,
    "التاسعة عشره": 19,
}

ORDINAL_TENS_MAP = {
    "العشرون": 20,
    "العشرين": 20,
    "الثلاثون": 30,
    "الثلاثين": 30,
    "الاربعون": 40,
    "الأربعون": 40,
    "الاربعين": 40,
    "الأربعين": 40,
    "الخمسون": 50,
    "الخمسين": 50,
    "الستون": 60,
    "الستين": 60,
    "السبعون": 70,
    "السبعين": 70,
    "الثمانون": 80,
    "الثمانين": 80,
    "التسعون": 90,
    "التسعين": 90,
}


def normalize_ordinal_part(part: str) -> str:
    part = part.strip()
    if part.startswith("ل") and f"ا{part}" in ORDINAL_UNIT_MAP:
        return f"ا{part}"
    return part


def _load_catalog_entries(path: Path) -> list[dict]:
    if not path.exists():
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload.get("entries", [])


def load_catalog() -> list[dict]:
    merged_entries = []
    seen_slugs = set()

    for source_name, path in (
        ("official_catalog", CATALOG_PATH),
        ("custom_catalog", CUSTOM_CATALOG_PATH),
    ):
        for raw_entry in _load_catalog_entries(path):
            entry = dict(raw_entry)
            slug = entry.get("slug", "").strip()
            if not slug or slug in seen_slugs:
                continue
            seen_slugs.add(slug)
            entry.setdefault("status", "قيد المراجعة")
            entry.setdefault("issue_date_hijri", "")
            entry.setdefault("issue_date_gregorian", "")
            entry.setdefault("publish_date_hijri", "")
            entry.setdefault("publish_date_gregorian", "")
            entry.setdefault("official_source_urls", [])
            entry.setdefault("knowledge_filename", f"{slug}.txt")
            entry.setdefault("organized_filename", f"{slug}.pdf")
            entry.setdefault("document_scope", "uploaded_reference")
            entry.setdefault("related_regulations", [])
            entry["catalog_source"] = source_name
            merged_entries.append(entry)

    return merged_entries


def resolve_source_path(entry: dict) -> Path:
    source_relpath = (entry.get("source_relpath") or "").strip()
    if source_relpath:
        return ROOT / source_relpath

    local_source_relpath = (entry.get("local_source_relpath") or "").strip()
    if local_source_relpath:
        local_candidate = ROOT / local_source_relpath
        if local_candidate.exists():
            return local_candidate

    organized_filename = entry.get("organized_filename", "")
    if organized_filename:
        onboarded_candidate = ONBOARDED_DIR / organized_filename
        if onboarded_candidate.exists():
            return onboarded_candidate
        return OFFICIAL_DIR / organized_filename

    return OFFICIAL_DIR / f"{entry['slug']}.pdf"


def ensure_dirs() -> None:
    KNOWLEDGE_DIR.mkdir(parents=True, exist_ok=True)
    BY_REGULATION_DIR.mkdir(parents=True, exist_ok=True)
    STRUCTURED_DIR.mkdir(parents=True, exist_ok=True)
    OFFICIAL_SNAPSHOTS_DIR.mkdir(parents=True, exist_ok=True)
    VERBATIM_DIR.mkdir(parents=True, exist_ok=True)
    ONBOARDED_DIR.mkdir(parents=True, exist_ok=True)
    TESSDATA_DIR.mkdir(parents=True, exist_ok=True)


def clear_generated_outputs() -> None:
    for path in [
        REGULATIONS_JSON,
        REGULATIONS_CSV,
        ARTICLES_JSONL,
        ARTICLES_CSV,
        PARAGRAPHS_JSONL,
        PARAGRAPHS_CSV,
        CHUNKS_JSONL,
        CHUNKS_CSV,
        REPORT_JSON,
    ]:
        if path.exists():
            path.unlink()
    if BY_REGULATION_DIR.exists():
        for child in BY_REGULATION_DIR.iterdir():
            if child.is_file():
                child.unlink()
    if VERBATIM_DIR.exists():
        for child in VERBATIM_DIR.iterdir():
            if child.is_file():
                child.unlink()
    if KNOWLEDGE_DIR.exists():
        for child in KNOWLEDGE_DIR.iterdir():
            if child.is_file():
                child.unlink()


def prepare_verbatim_text(text: str) -> str:
    text = html.unescape(text or "")
    text = CONTROL_CHAR_PATTERN.sub("", text)
    text = text.replace("\u00a0", " ")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ ]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def normalize_text(text: str) -> str:
    text = prepare_verbatim_text(text)
    text = unicodedata.normalize("NFKC", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def ensure_ocr_language_data(language: str) -> bool:
    target_path = TESSDATA_DIR / f"{language}.traineddata"
    if target_path.exists():
        return True

    for source_dir in COMMON_TESSDATA_DIRS:
        candidate = source_dir / f"{language}.traineddata"
        if candidate.exists():
            try:
                shutil.copyfile(candidate, target_path)
                return True
            except Exception:
                pass

    download_url = OCR_LANGUAGE_DOWNLOAD_URLS.get(language)
    if not download_url or not shutil.which("curl"):
        return False

    try:
        subprocess.run(
            ["curl", "-L", download_url, "-o", str(target_path)],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception:
        return False
    return target_path.exists()


def extract_pdf_text_via_ocr(path: Path) -> str:
    if not shutil.which("pdftoppm") or not shutil.which("tesseract"):
        return ""
    if not ensure_ocr_language_data("ara"):
        return ""

    has_english = ensure_ocr_language_data("eng")
    languages = "ara+eng" if has_english else "ara"
    ocr_env = dict(os.environ)
    ocr_env["TESSDATA_PREFIX"] = str(TESSDATA_DIR)
    page_texts: list[str] = []

    try:
        with tempfile.TemporaryDirectory(prefix="ocr-", dir=str(STRUCTURED_DIR)) as temp_dir:
            temp_path = Path(temp_dir)
            image_prefix = temp_path / "page"
            subprocess.run(
                ["pdftoppm", "-r", "220", "-png", str(path), str(image_prefix)],
                check=True,
                capture_output=True,
                text=True,
            )
            image_paths = sorted(temp_path.glob("page-*.png"))
            for image_path in image_paths:
                result = subprocess.run(
                    ["tesseract", str(image_path), "stdout", "-l", languages, "--psm", "6"],
                    check=True,
                    capture_output=True,
                    text=True,
                    env=ocr_env,
                )
                page_text = prepare_verbatim_text(result.stdout)
                if page_text:
                    page_texts.append(page_text)
    except Exception:
        return ""

    return "\n\n".join(page_texts).strip()


def extract_pdf_text(path: Path) -> str:
    text = ""
    if PdfReader is not None:
        try:
            reader = PdfReader(str(path))
            pieces = []
            for page in reader.pages:
                pieces.append(page.extract_text() or "")
            text = "\n".join(pieces).strip()
        except Exception:
            text = ""

    if len(text) < 400 and shutil.which("pdftotext"):
        try:
            result = subprocess.run(
                ["pdftotext", "-layout", str(path), "-"],
                check=True,
                capture_output=True,
                text=True,
            )
            text = result.stdout.strip() or text
        except Exception:
            pass

    if len(text) < 400:
        ocr_text = extract_pdf_text_via_ocr(path)
        if len(ocr_text) > len(text):
            text = ocr_text

    return text


def extract_docx_text(path: Path) -> str:
    text = ""
    if DocxDocument is not None:
        try:
            doc = DocxDocument(str(path))
            text = "\n".join(paragraph.text for paragraph in doc.paragraphs).strip()
        except Exception:
            text = ""

    if len(text) < 100 and shutil.which("textutil"):
        try:
            result = subprocess.run(
                ["textutil", "-convert", "txt", "-stdout", str(path)],
                check=True,
                capture_output=True,
                text=True,
            )
            text = result.stdout.strip() or text
        except Exception:
            pass

    return text


def extract_text(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".txt":
        return path.read_text(encoding="utf-8", errors="ignore")
    if suffix == ".pdf":
        return extract_pdf_text(path)
    if suffix == ".docx":
        return extract_docx_text(path)
    return ""


def fetch_url_text(url: str) -> str:
    result = subprocess.run(
        [
            "curl",
            "-sL",
            "-A",
            "Mozilla/5.0 (compatible; CodexLegalIndexer/1.0)",
            url,
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout


def normalize_match_text(value: str) -> str:
    return normalize_text(value).strip()


def extract_label_value(page_html: str, label_text: str) -> str:
    pattern = re.compile(
        rf"<label[^>]*>\s*{re.escape(label_text)}\s*</label>\s*<span[^>]*>\s*(.*?)\s*</span>",
        re.S,
    )
    match = pattern.search(page_html)
    return normalize_match_text(match.group(1)) if match else ""


def strip_tags(fragment: str) -> str:
    text = re.sub(r"(?i)<br\s*/?>", "\n", fragment)
    text = re.sub(r"(?i)</p>", "\n", text)
    text = re.sub(r"(?i)</li>", "\n", text)
    text = re.sub(r"(?i)<li[^>]*>", "• ", text)
    text = re.sub(r"(?i)</ol>|</ul>", "\n", text)
    text = re.sub(r"<[^>]+>", "", text)
    return prepare_verbatim_text(text)


def normalize_boe_article_title(raw_title: str) -> str:
    title = normalize_text(strip_tags(raw_title))
    title = re.sub(r"^(?:المادة|مادة)\s+", "", title).strip()
    return title


def search_boe_source_url(title_ar: str) -> str | None:
    query = urllib.parse.quote(title_ar)
    search_url = f"https://laws.boe.gov.sa/BoeLaws/Laws/Search/?LanguageId=1&Query={query}&SearchTypeId=3"
    page_html = fetch_url_text(search_url)
    for match in RESULT_ITEM_RE.finditer(page_html):
        result_title = normalize_match_text(match.group("title"))
        if result_title == title_ar:
            href = html.unescape(match.group("search_href"))
            return urllib.parse.urljoin("https://laws.boe.gov.sa", href)
    return None


def _normalize_uqn_body_text(text: str) -> str:
    text = html.unescape(text or "")
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"\s+(?=(?:الباب|الفصل|المادة)\s+[^\n]{0,80}?:)", "\n\n", text)
    return prepare_verbatim_text(text)


def _extract_uqn_api_url(page_html: str) -> str | None:
    match = UQN_WIDGET_URL_RE.search(page_html or "")
    if match:
        return match.group("url")
    match = UQN_CMS_ARTICLE_ID_RE.search(page_html or "")
    if match:
        cms_article_id = match.group("id")
        return f"https://www.uqn.gov.sa/api/article/{cms_article_id}/json"
    return None


def _strip_uqn_article_html(fragment: str) -> str:
    text = fragment or ""
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</div>|</p>|</li>|</tr>|</table>|</section>|</article>", "\n", text)
    text = re.sub(r"(?i)<div[^>]*>|<p[^>]*>|<tr[^>]*>|<table[^>]*>|<section[^>]*>|<article[^>]*>", "\n", text)
    text = re.sub(r"(?i)<li[^>]*>", "• ", text)
    text = re.sub(r"(?i)</ol>|</ul>", "\n", text)
    text = re.sub(r"<[^>]+>", "", text)
    return prepare_verbatim_text(text)


def _build_uqn_extract_from_html(entry: dict, page_html: str, resolved_url: str) -> dict | None:
    title_match = UQN_ARTICLE_TITLE_RE.search(page_html or "")
    extracted_title = normalize_match_text(strip_tags(title_match.group("title"))) if title_match else ""
    article_match = UQN_ARTICLE_CONTENT_RE.search(page_html or "")
    if not article_match:
        return None

    article_body_clean = _normalize_uqn_body_text(_strip_uqn_article_html(article_match.group("body")))
    if not article_body_clean:
        return None

    parsed_articles = split_articles(normalize_text(article_body_clean))
    if parsed_articles:
        articles = [
            {
                "article_label": parsed_article["article_label"],
                "text_verbatim": prepare_verbatim_text(parsed_article["text"]),
            }
            for parsed_article in parsed_articles
            if parsed_article.get("article_label") and parsed_article.get("text")
        ]
    else:
        articles = [{"article_label": "النص الكامل", "text_verbatim": article_body_clean}]

    date_match = UQN_DATE_RE.search(page_html or "")
    publish_hijri = normalize_match_text(date_match.group("hijri")) if date_match else ""
    publish_gregorian_raw = normalize_match_text(date_match.group("gregorian")) if date_match else ""
    publish_gregorian = ""
    if publish_gregorian_raw:
        gregorian_parts = [part.zfill(2) for part in publish_gregorian_raw.split("-")]
        if len(gregorian_parts) == 3:
            publish_gregorian = f"{gregorian_parts[2]}-{gregorian_parts[1]}-{gregorian_parts[0]}"

    return {
        "resolved_url": resolved_url,
        "title_ar": entry.get("title_ar") or extracted_title,
        "status": entry.get("status", "ساري"),
        "issue_date_hijri": entry.get("issue_date_hijri", "") or publish_hijri,
        "issue_date_gregorian": entry.get("issue_date_gregorian", "") or publish_gregorian,
        "publish_date_hijri": entry.get("publish_date_hijri", "") or publish_hijri,
        "publish_date_gregorian": entry.get("publish_date_gregorian", "") or publish_gregorian,
        "articles": articles,
        "text_verbatim": article_body_clean,
    }


def _build_uqn_extract_from_payload(entry: dict, payload: dict, resolved_url: str) -> dict | None:
    articles_payload = payload.get("articles") or []
    if not articles_payload:
        return None

    article = articles_payload[0]
    source_title = normalize_match_text(article.get("article_title", ""))
    title_ar = entry.get("title_ar") or source_title
    article_body_clean = _normalize_uqn_body_text(article.get("article_body_clean", ""))
    if not article_body_clean:
        return None
    if article_body_clean.endswith("...") or article_body_clean.endswith("…"):
        return None

    parsed_articles = split_articles(normalize_text(article_body_clean))
    if parsed_articles:
        articles = [
            {
                "article_label": parsed_article["article_label"],
                "text_verbatim": prepare_verbatim_text(parsed_article["text"]),
            }
            for parsed_article in parsed_articles
            if parsed_article.get("article_label") and parsed_article.get("text")
        ]
    else:
        articles = [{"article_label": "النص الكامل", "text_verbatim": article_body_clean}]

    publish_hijri = normalize_match_text(article.get("hijri_date", "")).replace("-", "/")
    publish_gregorian_raw = normalize_match_text(article.get("gregorian_date", ""))
    publish_gregorian = ""
    if publish_gregorian_raw:
        gregorian_parts = [part.zfill(2) for part in publish_gregorian_raw.split("-")]
        if len(gregorian_parts) == 3:
            publish_gregorian = f"{gregorian_parts[2]}-{gregorian_parts[1]}-{gregorian_parts[0]}"

    status = entry.get("status", "ساري")
    if source_title.startswith("مشروع نظام"):
        status = entry.get("status", "ساري")

    return {
        "resolved_url": article.get("permalink") or resolved_url,
        "title_ar": title_ar,
        "status": status,
        "issue_date_hijri": entry.get("issue_date_hijri", "") or publish_hijri,
        "issue_date_gregorian": entry.get("issue_date_gregorian", "") or publish_gregorian,
        "publish_date_hijri": entry.get("publish_date_hijri", "") or publish_hijri,
        "publish_date_gregorian": entry.get("publish_date_gregorian", "") or publish_gregorian,
        "articles": articles,
        "text_verbatim": article_body_clean,
    }


def _build_uqn_extract(entry: dict, page_html: str, resolved_url: str) -> dict | None:
    api_url = _extract_uqn_api_url(page_html)
    api_snapshot_path = OFFICIAL_SNAPSHOTS_DIR / f"{entry['slug']}.uqn.json"

    if api_snapshot_path.exists():
        try:
            payload = json.loads(api_snapshot_path.read_text(encoding="utf-8"))
        except Exception:
            payload = None
        if isinstance(payload, dict):
            extracted = _build_uqn_extract_from_payload(entry, payload, resolved_url)
            if extracted:
                return extracted

    if not api_url:
        return _build_uqn_extract_from_html(entry, page_html, resolved_url)

    try:
        payload = json.loads(fetch_url_text(api_url))
    except Exception:
        return _build_uqn_extract_from_html(entry, page_html, resolved_url)

    try:
        api_snapshot_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass

    return _build_uqn_extract_from_payload(entry, payload, resolved_url) or _build_uqn_extract_from_html(
        entry,
        page_html,
        resolved_url,
    )


def _build_sdaia_knowledge_extract(entry: dict, page_html: str, resolved_url: str) -> dict | None:
    section_match = SDAIA_KNOWLEDGE_SECTION_RE.search(page_html or "")
    if not section_match:
        return None

    section_html = section_match.group("body")
    title_match = re.search(r"<h[12][^>]*>(?P<title>.*?)</h[12]>", section_html, re.S)
    extracted_title = normalize_match_text(strip_tags(title_match.group("title"))) if title_match else ""
    title_ar = entry.get("title_ar") or extracted_title

    cleaned_section_html = re.sub(r"<a[^>]*class=\"dgp-button[^\"]*\"[^>]*>.*?</a>", "", section_html, flags=re.S)
    cleaned_section_html = re.sub(r"<div[^>]*class=\"knowledge-page__back[^\"]*\"[^>]*>.*?</div>", "", cleaned_section_html, flags=re.S)
    text_verbatim = prepare_verbatim_text(strip_tags(cleaned_section_html))
    if not text_verbatim:
        return None

    parsed_articles = split_articles(normalize_text(text_verbatim))
    if parsed_articles:
        articles = [
            {
                "article_label": article["article_label"],
                "text_verbatim": prepare_verbatim_text(article["text"]),
            }
            for article in parsed_articles
            if article.get("article_label") and article.get("text")
        ]
    else:
        articles = [{"article_label": "النص الكامل", "text_verbatim": text_verbatim}]

    return {
        "resolved_url": resolved_url,
        "title_ar": title_ar or entry.get("title_ar", ""),
        "status": entry.get("status", "ساري"),
        "issue_date_hijri": entry.get("issue_date_hijri", ""),
        "issue_date_gregorian": entry.get("issue_date_gregorian", ""),
        "publish_date_hijri": entry.get("publish_date_hijri", ""),
        "publish_date_gregorian": entry.get("publish_date_gregorian", ""),
        "articles": articles,
        "text_verbatim": text_verbatim,
    }


def _build_boe_extract_from_page(entry: dict, page_html: str, resolved_url: str) -> dict | None:
    if any(marker in page_html for marker in INVALID_BOE_PAGE_MARKERS):
        return None

    official_title = ""
    page_title_match = re.search(r'<h1 class="page-title">(.*?)</h1>', page_html, re.S)
    if page_title_match:
        official_title = normalize_match_text(page_title_match.group(1))

    if 'class="article_item' in page_html:
        articles = []
        for match in ARTICLE_BLOCK_RE.finditer(page_html):
            title = normalize_boe_article_title(match.group("title"))
            body = strip_tags(match.group("body"))
            if not title or not body:
                continue
            articles.append({"article_label": title, "text_verbatim": body})
        if articles:
            full_text = "\n\n".join(
                f"المادة {article['article_label']}:\n{article['text_verbatim']}" for article in articles
            )
            return {
                "resolved_url": resolved_url,
                "title_ar": official_title or entry["title_ar"],
                "status": extract_label_value(page_html, "الحالة") or entry["status"],
                "issue_date_hijri": extract_label_value(page_html, "تاريخ الإصدار") or entry["issue_date_hijri"],
                "publish_date_hijri": extract_label_value(page_html, "تاريخ النشر") or entry["publish_date_hijri"],
                "articles": articles,
                "text_verbatim": prepare_verbatim_text(full_text),
            }

    law_text_match = LAW_TEXTBOX_RE.search(page_html)
    if not law_text_match:
        return None

    law_text = prepare_verbatim_text(strip_tags(law_text_match.group("body")))
    if not law_text:
        return None

    parsed_articles = split_articles(normalize_text(law_text))
    if parsed_articles:
        articles = [
            {
                "article_label": article["article_label"],
                "text_verbatim": prepare_verbatim_text(article["text"]),
            }
            for article in parsed_articles
            if article["article_label"] and article["text"]
        ]
    else:
        articles = [{"article_label": "النص الكامل", "text_verbatim": law_text}]

    return {
        "resolved_url": resolved_url,
        "title_ar": official_title or entry["title_ar"],
        "status": extract_label_value(page_html, "الحالة") or entry["status"],
        "issue_date_hijri": extract_label_value(page_html, "تاريخ الإصدار") or entry["issue_date_hijri"],
        "publish_date_hijri": extract_label_value(page_html, "تاريخ النشر") or entry["publish_date_hijri"],
        "articles": articles,
        "text_verbatim": law_text,
    }


def extract_official_boe_content(entry: dict) -> dict | None:
    primary_url = entry["official_source_urls"][0] if entry["official_source_urls"] else ""
    candidate_urls = [primary_url] if primary_url else []
    snapshot_path = OFFICIAL_SNAPSHOTS_DIR / f"{entry['slug']}.html"

    if "dgp.sdaia.gov.sa" in primary_url:
        if snapshot_path.exists():
            page_html = snapshot_path.read_text(encoding="utf-8")
            snapshot_extract = _build_sdaia_knowledge_extract(entry, page_html, primary_url)
            if snapshot_extract:
                snapshot_extract["snapshot_path"] = snapshot_path
                return snapshot_extract

        for url in candidate_urls:
            if not url or "dgp.sdaia.gov.sa" not in url:
                continue
            try:
                page_html = fetch_url_text(url)
            except Exception:
                continue
            extracted = _build_sdaia_knowledge_extract(entry, page_html, url)
            if not extracted:
                continue
            snapshot_path.write_text(page_html, encoding="utf-8")
            extracted["snapshot_path"] = snapshot_path
            return extracted
        return None

    if "uqn.gov.sa" in primary_url:
        if snapshot_path.exists():
            page_html = snapshot_path.read_text(encoding="utf-8")
            snapshot_extract = _build_uqn_extract(entry, page_html, primary_url)
            if snapshot_extract:
                snapshot_extract["snapshot_path"] = snapshot_path
                return snapshot_extract

        for url in candidate_urls:
            if not url or "uqn.gov.sa" not in url:
                continue
            try:
                page_html = fetch_url_text(url)
            except Exception:
                continue
            extracted = _build_uqn_extract(entry, page_html, url)
            if not extracted:
                continue
            snapshot_path.write_text(page_html, encoding="utf-8")
            extracted["snapshot_path"] = snapshot_path
            return extracted
        return None

    if snapshot_path.exists():
        page_html = snapshot_path.read_text(encoding="utf-8")
        snapshot_extract = _build_boe_extract_from_page(entry, page_html, primary_url)
        if snapshot_extract:
            snapshot_extract["snapshot_path"] = snapshot_path
            return snapshot_extract

    if "laws.boe.gov.sa" in primary_url:
        try:
            discovered_url = search_boe_source_url(entry["title_ar"])
        except Exception:
            discovered_url = None
        if discovered_url:
            candidate_urls.insert(0, discovered_url)

    for url in candidate_urls:
        if not url or "laws.boe.gov.sa" not in url:
            continue
        try:
            page_html = fetch_url_text(url)
        except Exception:
            continue

        extracted = _build_boe_extract_from_page(entry, page_html, url)
        if not extracted:
            continue

        snapshot_path.write_text(page_html, encoding="utf-8")
        extracted["snapshot_path"] = snapshot_path
        return extracted

    return None


def split_articles(text: str) -> list[dict]:
    articles = []
    current_label = None
    current_lines = []

    def looks_like_article_label(label: str) -> bool:
        label = label.strip()
        if not label:
            return False
        return label[0] in "اأإآ(0123456789٠١٢٣٤٥٦٧٨٩"

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            if current_label is not None:
                current_lines.append("")
            continue

        cleaned = re.sub(r"^[#*\-•]+\s*", "", line)
        match = ARTICLE_RE.match(cleaned) or INLINE_ARTICLE_RE.search(cleaned)
        has_explicit_article_separator = bool(
            re.match(
                r"^(?:المادة|املادة|اﻟﻤﺎدة|مادة)\s+[^\s:]{1,40}(?:\s+[^\s:]{1,20}){0,3}\s*[:：]",
                cleaned,
            )
        )
        if match and looks_like_article_label(match.group(1)) and (len(cleaned) < 220 or has_explicit_article_separator):
            if current_label is not None:
                body = "\n".join(current_lines).strip()
                articles.append(
                    {
                        "article_label": current_label,
                        "text": body,
                    }
                )
            current_label = match.group(1).strip()
            current_lines = []
            if match.group(2).strip():
                current_lines.append(match.group(2).strip())
            continue

        if current_label is not None:
            current_lines.append(line)

    if current_label is not None:
        body = "\n".join(current_lines).strip()
        articles.append(
            {
                "article_label": current_label,
                "text": body,
            }
        )

    return [article for article in articles if article["text"]]


def split_paragraphs(text: str) -> list[str]:
    paragraphs = [part.strip() for part in re.split(r"\n\s*\n", text) if part.strip()]
    if paragraphs:
        return paragraphs
    return [text.strip()] if text.strip() else []


def split_verbatim_paragraphs(text: str, target_chars: int = 700) -> list[str]:
    base_blocks = split_paragraphs(prepare_verbatim_text(text))
    if not base_blocks:
        return []

    refined_blocks = []
    for block in base_blocks:
        if len(block) <= target_chars:
            refined_blocks.append(block)
            continue

        lines = [line.rstrip() for line in block.splitlines() if line.strip()]
        if len(lines) > 1:
            current_lines = []
            current_len = 0
            for line in lines:
                line_len = len(line)
                if current_lines and current_len + line_len > target_chars:
                    refined_blocks.append("\n".join(current_lines).strip())
                    current_lines = [line]
                    current_len = line_len
                else:
                    current_lines.append(line)
                    current_len += line_len + 1
            if current_lines:
                refined_blocks.append("\n".join(current_lines).strip())
            continue

        sentences = re.split(r"(?<=[.؟!])\s+", block)
        current_sentences = []
        current_len = 0
        for sentence in sentences:
            sentence = sentence.strip()
            if not sentence:
                continue
            sentence_len = len(sentence)
            if current_sentences and current_len + sentence_len > target_chars:
                refined_blocks.append(" ".join(current_sentences).strip())
                current_sentences = [sentence]
                current_len = sentence_len
            else:
                current_sentences.append(sentence)
                current_len += sentence_len + 1
        if current_sentences:
            refined_blocks.append(" ".join(current_sentences).strip())

    return [block for block in refined_blocks if block]


def _contains_any_keyword(text: str, keywords: list[str]) -> bool:
    return any(keyword in text for keyword in keywords)


def infer_article_type(article_heading: str, article_text: str) -> str:
    heading = normalize_text(article_heading).lower()
    content = normalize_text(article_text).lower()
    full_text = f"{heading}\n{content}".strip()
    first_paragraph = next((line.strip() for line in content.splitlines() if line.strip()), "")

    for article_type, keywords in ARTICLE_TYPE_HEADING_RULES:
        if _contains_any_keyword(heading, keywords):
            return article_type

    for article_type, keywords in ARTICLE_TYPE_TEXT_RULES:
        if _contains_any_keyword(first_paragraph, keywords):
            return article_type
        if _contains_any_keyword(full_text, keywords):
            return article_type

    if "اذا" in first_paragraph and "فله" in first_paragraph:
        return "rights"
    if "اذا" in first_paragraph and "ما لم" in full_text:
        return "exception"
    if "على كل" in first_paragraph or first_paragraph.startswith("يلتزم"):
        return "condition"
    return "general"


def infer_topic_tags(regulation_slug: str, article_heading: str, article_text: str) -> list[str]:
    content = normalize_text(f"{article_heading}\n{article_text}").lower()
    tags = []
    for tag, keywords in TOPIC_TAG_RULES:
        if any(keyword in content for keyword in keywords):
            tags.append(tag)

    tags.extend(REGULATION_DEFAULT_TOPIC_TAGS.get(regulation_slug, []))
    allowed_tags = REGULATION_ALLOWED_TOPIC_TAGS.get(regulation_slug)
    if allowed_tags is not None:
        tags = [tag for tag in tags if tag in allowed_tags]

    return list(dict.fromkeys(tags))


def infer_legal_function_tags(
    article_heading: str,
    article_text: str,
    article_type: str,
) -> list[str]:
    content = normalize_text(f"{article_heading}\n{article_text}").lower()
    tags = list(ARTICLE_TYPE_DEFAULT_FUNCTIONS.get(article_type, []))
    for tag, keywords in LEGAL_FUNCTION_RULES:
        if any(keyword in content for keyword in keywords):
            tags.append(tag)
    return list(dict.fromkeys(tags))


def infer_version_status(entry: dict, verbatim_text: str) -> str:
    if entry.get("recent_update_note"):
        return "current_official_text_recent_update_pending_publication"
    normalized_text = normalize_text(verbatim_text).lower()
    if any(
        marker in normalized_text
        for marker in ("تم تعديل", "عدلت هذه المادة", "المرسوم الملكي", "قرار مجلس الوزراء")
    ):
        return "current_official_text_with_embedded_amendment_notes"
    return "current_official_text"


def build_contextual_header(
    title_ar: str,
    citation_short_ar: str,
    article_type: str,
    topic_tags: list[str],
    legal_function_tags: list[str],
    version_status_label_ar: str = "",
) -> str:
    article_type_label = ARTICLE_TYPE_LABELS_AR.get(article_type, "حكم عام")
    topic_labels = [TOPIC_TAG_LABELS_AR.get(tag, tag) for tag in topic_tags]
    function_labels = [LEGAL_FUNCTION_LABELS_AR.get(tag, tag) for tag in legal_function_tags]
    lines = [
        f"النظام: {title_ar}",
        f"الإحالة: {citation_short_ar}",
        f"نوع المادة: {article_type_label}",
    ]
    if function_labels:
        lines.append(f"الوظائف القانونية: {', '.join(function_labels)}")
    if topic_labels:
        lines.append(f"الموضوعات: {', '.join(topic_labels)}")
    if version_status_label_ar:
        lines.append(f"الحالة الزمنية: {version_status_label_ar}")
    return "\n".join(lines).strip()


def clean_article_heading(raw_heading: str) -> str:
    heading = normalize_text(raw_heading)
    heading = heading.replace(" :", ":").strip(":- \n\t")
    replacements = [
        ("لأ", "الأ"),
        ("ألو", "الأو"),
        ("األو", "الأو"),
        ("ألاو", "الأو"),
        ("لث", "الث"),
        ("لت", "الت"),
        ("لس", "الس"),
        ("لع", "الع"),
        ("لر", "الر"),
        ("حلاد", "الحاد"),
        ("حلادي", "الحادي"),
        ("خلا", "الخا"),
        ("اا", "ال"),
    ]
    for old, new in replacements:
        if heading.startswith(old):
            heading = new + heading[len(old):]
            break
    heading = re.sub(r"\s{2,}", " ", heading).strip()
    return heading


def parse_arabic_article_number(article_heading: str) -> int | None:
    heading = normalize_text(article_heading)
    heading = re.sub(r"^(?:المادة|مادة)\s+", "", heading).strip(" :.-")
    heading = re.split(r"\s*[:：]\s*", heading, maxsplit=1)[0].strip()
    if not heading:
        return None

    digit_match = re.search(r"\d{1,4}", heading)
    if digit_match:
        return int(digit_match.group())

    normalized = heading.replace("أ", "ا").replace("إ", "ا").replace("آ", "ا")
    normalized = re.sub(r"[^\w\s]", " ", normalized)
    normalized = re.sub(r"\s{2,}", " ", normalized).strip()

    if "بعد المائه" in normalized:
        before, _, _ = normalized.partition("بعد المائه")
        base = 100
        segment = before.strip()
    elif "بعد المائة" in normalized:
        before, _, _ = normalized.partition("بعد المائة")
        base = 100
        segment = before.strip()
    else:
        base = 0
        segment = normalized.strip()

    if not segment:
        return base or None

    if segment in ORDINAL_TEENS_MAP:
        return base + ORDINAL_TEENS_MAP[segment]
    if segment in ORDINAL_TENS_MAP:
        return base + ORDINAL_TENS_MAP[segment]
    normalized_segment = normalize_ordinal_part(segment)
    if normalized_segment in ORDINAL_UNIT_MAP:
        return base + ORDINAL_UNIT_MAP[normalized_segment]

    parts = [part.strip() for part in re.split(r"\s+و", segment) if part.strip()]
    if len(parts) == 2:
        total = 0
        for part in parts:
            part = normalize_ordinal_part(part)
            if part in ORDINAL_TEENS_MAP:
                total += ORDINAL_TEENS_MAP[part]
                continue
            if part in ORDINAL_TENS_MAP:
                total += ORDINAL_TENS_MAP[part]
                continue
            if part in ORDINAL_UNIT_MAP:
                total += ORDINAL_UNIT_MAP[part]
                continue
            return None
        return base + total

    return None


def derive_article_heading(article_label: str, article_text: str) -> tuple[str, str]:
    raw_heading = article_label.strip()
    first_line = article_text.splitlines()[0].strip() if article_text.splitlines() else ""

    if len(re.sub(r"\W", "", raw_heading)) <= 1 and first_line:
        match = re.match(r"^([^\s:]{1,40}(?:\s+[^\s:]{1,20}){0,3})\s*[:：]", first_line)
        if match:
            raw_heading = match.group(1).strip()

    cleaned_heading = clean_article_heading(raw_heading)
    return raw_heading, cleaned_heading or raw_heading


def build_article_citation(title_ar: str, article_heading: str) -> str:
    heading = article_heading.strip()
    if heading:
        return f"{title_ar}، المادة {heading}"
    return f"{title_ar}، مادة غير معنونّة"


def is_low_signal_paragraph(text: str) -> bool:
    normalized = normalize_text(text)
    if not normalized:
        return True
    if LOW_SIGNAL_PARAGRAPH_RE.fullmatch(normalized):
        return True
    return False


def build_paragraph_records(
    regulation_slug: str,
    article_index: int,
    article_heading: str,
    article_type: str,
    article_text_verbatim: str,
) -> list[dict]:
    records = []
    for paragraph_index, paragraph_text in enumerate(split_verbatim_paragraphs(article_text_verbatim), start=1):
        paragraph_verbatim = prepare_verbatim_text(paragraph_text)
        paragraph_index_text = normalize_text(paragraph_verbatim)
        if not paragraph_index_text:
            continue
        paragraph_function_tags = infer_legal_function_tags(article_heading, paragraph_verbatim, article_type)
        records.append(
            {
                "paragraph_id": f"{regulation_slug}::article-{article_index}::paragraph-{paragraph_index}",
                "paragraph_index": paragraph_index,
                "legal_function_tags": paragraph_function_tags,
                "legal_function_tags_ar": [LEGAL_FUNCTION_LABELS_AR.get(tag, tag) for tag in paragraph_function_tags],
                "text_verbatim": paragraph_verbatim,
                "text_for_index": paragraph_index_text,
                "indexable": not is_low_signal_paragraph(paragraph_index_text),
            }
        )
    return records


def build_chunk_records(
    title_ar: str,
    citation_short_ar: str,
    paragraph_records: list[dict],
    article_type: str,
    topic_tags: list[str],
    legal_function_tags: list[str],
    version_status_label_ar: str = "",
    max_chars: int = 1200,
) -> list[dict]:
    index_candidates = [paragraph for paragraph in paragraph_records if paragraph["indexable"]] or paragraph_records
    if not index_candidates:
        return []

    header = build_contextual_header(
        title_ar,
        citation_short_ar,
        article_type,
        topic_tags,
        legal_function_tags,
        version_status_label_ar,
    )
    chunk_records = []
    current_paragraphs = []
    current_index_blocks = [header]
    current_verbatim_parts = []

    def flush_current() -> None:
        if not current_paragraphs:
            return
        index_text = "\n\n".join(current_index_blocks).strip()
        verbatim_text = "\n\n".join(current_verbatim_parts).strip()
        chunk_function_tags = list(
            dict.fromkeys(
                tag
                for tag in legal_function_tags
                + [
                    paragraph_tag
                    for paragraph in current_paragraphs
                    for paragraph_tag in paragraph.get("legal_function_tags", [])
                ]
            )
        )
        chunk_records.append(
            {
                "contextual_header": header,
                "index_text": index_text,
                "text": index_text,
                "text_verbatim": verbatim_text,
                "paragraph_ids": [paragraph["paragraph_id"] for paragraph in current_paragraphs],
                "paragraph_indexes": [paragraph["paragraph_index"] for paragraph in current_paragraphs],
                "paragraph_count": len(current_paragraphs),
                "legal_function_tags": chunk_function_tags,
                "legal_function_tags_ar": [LEGAL_FUNCTION_LABELS_AR.get(tag, tag) for tag in chunk_function_tags],
            }
        )

    for paragraph in index_candidates:
        paragraph_block = f"[فقرة {paragraph['paragraph_index']}]\n{paragraph['text_for_index']}"
        candidate_blocks = current_index_blocks + [paragraph_block]
        candidate_text = "\n\n".join(candidate_blocks).strip()
        previous_function_tags = set(current_paragraphs[-1].get("legal_function_tags", [])) if current_paragraphs else set()
        next_function_tags = set(paragraph.get("legal_function_tags", []))
        function_shift = bool(
            current_paragraphs
            and previous_function_tags
            and next_function_tags
            and previous_function_tags != next_function_tags
        )

        if current_paragraphs and (len(candidate_text) > max_chars or function_shift):
            flush_current()
            current_paragraphs = []
            current_index_blocks = [header]
            current_verbatim_parts = []

        current_paragraphs.append(paragraph)
        current_index_blocks.append(paragraph_block)
        current_verbatim_parts.append(paragraph["text_verbatim"])

    flush_current()
    return chunk_records


def write_jsonl(path: Path, rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def write_csv(path: Path, rows: list[dict], fieldnames: list[str]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fieldnames})


def build_knowledge_text(entry: dict, articles: list[dict], verbatim_text: str) -> str:
    issue_date_line = ""
    if entry.get("issue_date_hijri") or entry.get("issue_date_gregorian"):
        issue_date_line = f"تاريخ الإصدار: {entry.get('issue_date_hijri', '')} هـ | {entry.get('issue_date_gregorian', '')}".strip()

    publish_date_line = ""
    if entry.get("publish_date_hijri") or entry.get("publish_date_gregorian"):
        publish_date_line = f"تاريخ النشر: {entry.get('publish_date_hijri', '')} هـ | {entry.get('publish_date_gregorian', '')}".strip()

    source_urls = [url for url in entry.get("official_source_urls", []) if url]
    source_label = "المصدر الرسمي" if source_urls else "المصدر المرجعي"
    source_value = " | ".join(source_urls) if source_urls else "غير متوفر"

    lines = [
        f"النظام: {entry['title_ar']}",
        f"الحالة: {entry['status']}",
        issue_date_line,
        publish_date_line,
        f"{source_label}: {source_value}",
        "",
    ]
    lines = [line for line in lines if line]

    if articles:
        for article in articles:
            lines.append(f"الإحالة: {article['citation_short_ar']}")
            lines.append(f"المادة {article['article_heading']}:")
            for paragraph in article["paragraphs"]:
                lines.append(paragraph["text_verbatim"])
                lines.append("")
            if lines[-1] == "":
                lines.pop()
            lines.append("")
    else:
        lines.append(verbatim_text)

    return "\n".join(lines).strip() + "\n"


def main() -> None:
    ensure_dirs()
    clear_generated_outputs()

    entries = load_catalog()
    regulations_rows = []
    article_rows = []
    paragraph_rows = []
    chunk_rows = []
    warnings = []

    for entry in entries:
        source_path = resolve_source_path(entry)
        official_extract = extract_official_boe_content(entry)
        if not source_path.exists() and not official_extract:
            warnings.append(
                {
                    "slug": entry["slug"],
                    "warning": "organized_source_missing",
                    "source": str(source_path.relative_to(ROOT)),
                }
            )
            continue

        extraction_source = "official_boe_html" if official_extract else "local_document_fallback"

        if official_extract:
            verbatim_text = official_extract["text_verbatim"]
        else:
            raw_text = extract_text(source_path)
            if entry["slug"] == "execution-implementing-regulation" and source_path.suffix.lower() == ".pdf":
                ocr_text = extract_pdf_text_via_ocr(source_path)
                if "المادة" in ocr_text and len(normalize_text(ocr_text)) > 400:
                    raw_text = ocr_text
                    warnings.append(
                        {
                            "slug": entry["slug"],
                            "warning": "ocr_text_used_for_weak_pdf_extraction",
                            "source": str(source_path.relative_to(ROOT)),
                        }
                    )
            verbatim_text = prepare_verbatim_text(raw_text)

        normalized_text = normalize_text(verbatim_text)
        verbatim_path = VERBATIM_DIR / f"{entry['slug']}.txt"
        verbatim_path.write_text(verbatim_text + ("\n" if verbatim_text and not verbatim_text.endswith("\n") else ""), encoding="utf-8")

        if len(normalized_text) < 400:
            warnings.append(
                {
                    "slug": entry["slug"],
                    "warning": "low_extraction_quality",
                    "chars": len(normalized_text),
                    "source": str(source_path.relative_to(ROOT)),
                }
            )

        articles = []
        if official_extract:
            parsed_articles = official_extract["articles"]
        else:
            parsed_articles = split_articles(normalized_text)
            if not parsed_articles and normalized_text:
                parsed_articles = [{"article_label": "النص الكامل", "text": verbatim_text}]
                warnings.append(
                    {
                        "slug": entry["slug"],
                        "warning": "synthetic_fulltext_article_created",
                        "chars": len(normalized_text),
                        "source": str(source_path.relative_to(ROOT)),
                    }
                )

        effective_title_ar = official_extract["title_ar"] if official_extract else entry["title_ar"]
        effective_status = official_extract["status"] if official_extract else entry["status"]
        effective_issue_date_hijri = official_extract["issue_date_hijri"] if official_extract else entry["issue_date_hijri"]
        effective_publish_date_hijri = official_extract["publish_date_hijri"] if official_extract else entry["publish_date_hijri"]
        effective_source_url = official_extract["resolved_url"] if official_extract else (entry["official_source_urls"][0] if entry["official_source_urls"] else "")
        version_status = infer_version_status(entry, verbatim_text)
        version_status_label_ar = VERSION_STATUS_LABELS_AR.get(version_status, version_status)
        effective_date_hijri = effective_publish_date_hijri or effective_issue_date_hijri
        effective_date_gregorian = entry["publish_date_gregorian"] or entry["issue_date_gregorian"]

        for index, article in enumerate(parsed_articles, start=1):
            if official_extract:
                article_text_verbatim = prepare_verbatim_text(article["text_verbatim"])
                article_label_raw = article["article_label"]
                article_heading = clean_article_heading(article["article_label"])

                if len(re.sub(r"\W", "", article_label_raw)) <= 1 and article_text_verbatim:
                    article_label_raw, article_heading = derive_article_heading(
                        article_label_raw,
                        article_text_verbatim,
                    )
                    first_line, *remaining_lines = article_text_verbatim.splitlines()
                    inline_heading_match = re.match(r"^([^\s:]{1,40}(?:\s+[^\s:]{1,20}){0,3})\s*[:：]\s*(.*)$", first_line.strip())
                    if inline_heading_match:
                        candidate_heading = clean_article_heading(inline_heading_match.group(1).strip())
                        if candidate_heading == article_heading:
                            normalized_lines = []
                            first_body = inline_heading_match.group(2).strip()
                            if first_body:
                                normalized_lines.append(first_body)
                            normalized_lines.extend(remaining_lines)
                            article_text_verbatim = "\n".join(normalized_lines).strip()
            else:
                article_label_raw, article_heading = derive_article_heading(
                    article["article_label"],
                    article["text"],
                )
                article_text_verbatim = prepare_verbatim_text(article["text"])

            parsed_article_index = parse_arabic_article_number(article_heading)
            article_index = parsed_article_index or index
            if parsed_article_index is not None and parsed_article_index != index:
                warnings.append(
                    {
                        "slug": entry["slug"],
                        "warning": "article_index_sequence_mismatch",
                        "article_heading": article_heading,
                        "sequence_index": index,
                        "parsed_article_index": parsed_article_index,
                    }
                )

            article_type = infer_article_type(article_heading, article_text_verbatim)
            paragraph_records = build_paragraph_records(
                entry["slug"],
                article_index,
                article_heading,
                article_type,
                article_text_verbatim,
            )
            citation_short_ar = build_article_citation(effective_title_ar, article_heading)
            topic_tags = infer_topic_tags(entry["slug"], article_heading, article_text_verbatim)
            legal_function_tags = infer_legal_function_tags(article_heading, article_text_verbatim, article_type)
            article_record = {
                "article_index": article_index,
                "article_label": article_heading,
                "article_label_raw": article_label_raw,
                "article_heading": article_heading,
                "article_type": article_type,
                "article_type_label_ar": ARTICLE_TYPE_LABELS_AR.get(article_type, "حكم عام"),
                "legal_function_tags": legal_function_tags,
                "legal_function_tags_ar": [LEGAL_FUNCTION_LABELS_AR.get(tag, tag) for tag in legal_function_tags],
                "topic_tags": topic_tags,
                "topic_tags_ar": [TOPIC_TAG_LABELS_AR.get(tag, tag) for tag in topic_tags],
                "citation_short_ar": citation_short_ar,
                "text_verbatim": article_text_verbatim,
                "text_for_index": normalize_text(article_text_verbatim),
                "paragraph_count": len(paragraph_records),
                "indexable_paragraph_count": sum(1 for paragraph in paragraph_records if paragraph["indexable"]),
                "paragraphs": paragraph_records,
            }
            articles.append(article_record)

        if not articles:
            warnings.append(
                {
                    "slug": entry["slug"],
                    "warning": "no_articles_extracted",
                    "chars": len(normalized_text),
                    "source": str(source_path.relative_to(ROOT)),
                }
            )

        entry_for_knowledge = {
            **entry,
            "title_ar": effective_title_ar,
            "status": effective_status,
            "issue_date_hijri": effective_issue_date_hijri,
            "publish_date_hijri": effective_publish_date_hijri,
            "official_source_urls": ([effective_source_url] if effective_source_url else []) + [url for url in entry["official_source_urls"] if url and url != effective_source_url],
        }
        knowledge_text = build_knowledge_text(entry_for_knowledge, articles, verbatim_text)
        knowledge_path = KNOWLEDGE_DIR / entry["knowledge_filename"]
        knowledge_path.write_text(knowledge_text, encoding="utf-8")

        total_paragraph_count = sum(article["paragraph_count"] for article in articles)
        total_indexable_paragraph_count = sum(article["indexable_paragraph_count"] for article in articles)

        regulation_row = {
            "slug": entry["slug"],
            "title_ar": effective_title_ar,
            "status": effective_status,
            "issue_date_hijri": effective_issue_date_hijri,
            "issue_date_gregorian": entry["issue_date_gregorian"],
            "publish_date_hijri": effective_publish_date_hijri,
            "publish_date_gregorian": entry["publish_date_gregorian"],
            "official_source_urls": [effective_source_url] + [url for url in entry["official_source_urls"] if url != effective_source_url],
            "organized_source": str(source_path.relative_to(ROOT)),
            "knowledge_file": str(knowledge_path.relative_to(ROOT)),
            "document_scope": entry["document_scope"],
            "related_regulations": entry.get("related_regulations", []),
            "catalog_source": entry.get("catalog_source", "official_catalog"),
            "version_status": version_status,
            "version_status_label_ar": version_status_label_ar,
            "effective_date_hijri": effective_date_hijri,
            "effective_date_gregorian": effective_date_gregorian,
            "historical_versions_available": False,
            "recent_update_note": entry.get("recent_update_note", ""),
            "article_count": len(articles),
            "paragraph_count": total_paragraph_count,
            "indexable_paragraph_count": total_indexable_paragraph_count,
            "text_chars": len(normalized_text),
            "verbatim_chars": len(verbatim_text),
            "citation_base_ar": effective_title_ar,
            "official_source_url_primary": effective_source_url,
            "extraction_source": extraction_source,
            "official_snapshot": str(official_extract["snapshot_path"].relative_to(ROOT)) if official_extract else "",
            "verbatim_file": str(verbatim_path.relative_to(ROOT)),
        }
        regulations_rows.append(regulation_row)

        for article in articles:
            article_rows.append(
                {
                    "regulation_slug": entry["slug"],
                    "regulation_title_ar": effective_title_ar,
                    "article_index": article["article_index"],
                    "article_label": article["article_label"],
                    "article_label_raw": article["article_label_raw"],
                    "article_heading": article["article_heading"],
                    "article_type": article["article_type"],
                    "article_type_label_ar": article["article_type_label_ar"],
                    "legal_function_tags": article["legal_function_tags"],
                    "legal_function_tags_ar": article["legal_function_tags_ar"],
                    "topic_tags": article["topic_tags"],
                    "topic_tags_ar": article["topic_tags_ar"],
                    "citation_short_ar": article["citation_short_ar"],
                    "paragraph_count": article["paragraph_count"],
                    "indexable_paragraph_count": article["indexable_paragraph_count"],
                    "paragraphs": article["paragraphs"],
                    "text_verbatim": article["text_verbatim"],
                    "text_for_index": article["text_for_index"],
                    "version_status": version_status,
                    "version_status_label_ar": version_status_label_ar,
                    "effective_date_hijri": effective_date_hijri,
                    "effective_date_gregorian": effective_date_gregorian,
                    "historical_versions_available": False,
                    "recent_update_note": entry.get("recent_update_note", ""),
                    "official_source_urls": regulation_row["official_source_urls"],
                    "official_source_url_primary": effective_source_url,
                    "related_regulations": entry.get("related_regulations", []),
                }
            )

            for paragraph in article["paragraphs"]:
                paragraph_rows.append(
                    {
                        "paragraph_id": paragraph["paragraph_id"],
                        "regulation_slug": entry["slug"],
                        "regulation_title_ar": effective_title_ar,
                        "article_index": article["article_index"],
                        "article_label": article["article_label"],
                        "article_label_raw": article["article_label_raw"],
                        "article_heading": article["article_heading"],
                        "article_type": article["article_type"],
                        "article_type_label_ar": article["article_type_label_ar"],
                        "legal_function_tags": paragraph["legal_function_tags"],
                        "legal_function_tags_ar": paragraph["legal_function_tags_ar"],
                        "topic_tags": article["topic_tags"],
                        "topic_tags_ar": article["topic_tags_ar"],
                        "citation_short_ar": article["citation_short_ar"],
                        "paragraph_index": paragraph["paragraph_index"],
                        "indexable": paragraph["indexable"],
                        "text_verbatim": paragraph["text_verbatim"],
                        "text_for_index": paragraph["text_for_index"],
                        "version_status": version_status,
                        "version_status_label_ar": version_status_label_ar,
                        "effective_date_hijri": effective_date_hijri,
                        "effective_date_gregorian": effective_date_gregorian,
                        "historical_versions_available": False,
                        "recent_update_note": entry.get("recent_update_note", ""),
                        "official_source_urls": regulation_row["official_source_urls"],
                        "official_source_url_primary": effective_source_url,
                        "related_regulations": entry.get("related_regulations", []),
                    }
                )

            for chunk_index, chunk_record in enumerate(
                build_chunk_records(
                    effective_title_ar,
                    article["citation_short_ar"],
                    article["paragraphs"],
                    article["article_type"],
                    article["topic_tags"],
                    article["legal_function_tags"],
                    version_status_label_ar,
                ),
                start=1,
            ):
                chunk_rows.append(
                    {
                        "chunk_id": f"{entry['slug']}::article-{article['article_index']}::chunk-{chunk_index}",
                        "regulation_slug": entry["slug"],
                        "regulation_title_ar": effective_title_ar,
                        "article_label": article["article_label"],
                        "article_label_raw": article["article_label_raw"],
                        "article_heading": article["article_heading"],
                        "article_type": article["article_type"],
                        "article_type_label_ar": article["article_type_label_ar"],
                        "legal_function_tags": chunk_record["legal_function_tags"],
                        "legal_function_tags_ar": chunk_record["legal_function_tags_ar"],
                        "topic_tags": article["topic_tags"],
                        "topic_tags_ar": article["topic_tags_ar"],
                        "citation_short_ar": article["citation_short_ar"],
                        "article_index": article["article_index"],
                        "chunk_index": chunk_index,
                        "contextual_header": chunk_record["contextual_header"],
                        "text": chunk_record["text"],
                        "index_text": chunk_record["index_text"],
                        "text_verbatim": chunk_record["text_verbatim"],
                        "paragraph_ids": chunk_record["paragraph_ids"],
                        "paragraph_indexes": chunk_record["paragraph_indexes"],
                        "paragraph_count": chunk_record["paragraph_count"],
                        "version_status": version_status,
                        "version_status_label_ar": version_status_label_ar,
                        "effective_date_hijri": effective_date_hijri,
                        "effective_date_gregorian": effective_date_gregorian,
                        "historical_versions_available": False,
                        "recent_update_note": entry.get("recent_update_note", ""),
                        "official_source_urls": regulation_row["official_source_urls"],
                        "official_source_url_primary": effective_source_url,
                        "related_regulations": entry.get("related_regulations", []),
                    }
                )

        BY_REGULATION_DIR.joinpath(f"{entry['slug']}.json").write_text(
            json.dumps(
                {
                    "metadata": regulation_row,
                    "articles": articles,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    REGULATIONS_JSON.write_text(
        json.dumps(regulations_rows, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    write_csv(
        REGULATIONS_CSV,
        regulations_rows,
        [
            "slug",
            "title_ar",
            "status",
            "issue_date_hijri",
            "issue_date_gregorian",
            "publish_date_hijri",
            "publish_date_gregorian",
            "effective_date_hijri",
            "effective_date_gregorian",
            "organized_source",
            "knowledge_file",
            "document_scope",
            "catalog_source",
            "version_status",
            "version_status_label_ar",
            "historical_versions_available",
            "recent_update_note",
            "article_count",
            "paragraph_count",
            "indexable_paragraph_count",
            "text_chars",
            "verbatim_chars",
            "citation_base_ar",
            "official_source_url_primary",
            "verbatim_file",
        ],
    )
    write_jsonl(ARTICLES_JSONL, article_rows)
    write_csv(
        ARTICLES_CSV,
        article_rows,
        [
            "regulation_slug",
            "regulation_title_ar",
            "article_index",
            "article_label",
            "article_label_raw",
            "article_heading",
            "article_type",
            "article_type_label_ar",
            "legal_function_tags_ar",
            "topic_tags_ar",
            "citation_short_ar",
            "paragraph_count",
            "indexable_paragraph_count",
            "version_status_label_ar",
            "effective_date_gregorian",
            "official_source_url_primary",
            "text_verbatim",
            "text_for_index",
        ],
    )
    write_jsonl(PARAGRAPHS_JSONL, paragraph_rows)
    write_csv(
        PARAGRAPHS_CSV,
        paragraph_rows,
        [
            "paragraph_id",
            "regulation_slug",
            "regulation_title_ar",
            "article_index",
            "article_label",
            "article_label_raw",
            "article_heading",
            "article_type",
            "article_type_label_ar",
            "legal_function_tags_ar",
            "topic_tags_ar",
            "citation_short_ar",
            "paragraph_index",
            "indexable",
            "version_status_label_ar",
            "effective_date_gregorian",
            "official_source_url_primary",
            "text_verbatim",
            "text_for_index",
        ],
    )
    write_jsonl(CHUNKS_JSONL, chunk_rows)
    write_csv(
        CHUNKS_CSV,
        chunk_rows,
        [
            "chunk_id",
            "regulation_slug",
            "regulation_title_ar",
            "article_index",
            "article_label",
            "article_label_raw",
            "article_heading",
            "article_type",
            "article_type_label_ar",
            "legal_function_tags_ar",
            "topic_tags_ar",
            "citation_short_ar",
            "chunk_index",
            "paragraph_count",
            "version_status_label_ar",
            "effective_date_gregorian",
            "official_source_url_primary",
            "text_verbatim",
            "text",
        ],
    )
    article_type_distribution = Counter(row["article_type"] for row in article_rows)
    legal_function_distribution = Counter(
        tag
        for row in article_rows
        for tag in row.get("legal_function_tags", [])
    )
    topic_tag_distribution = Counter(
        tag
        for row in article_rows
        for tag in row.get("topic_tags", [])
    )
    REPORT_JSON.write_text(
        json.dumps(
            {
                "regulations_processed": len(regulations_rows),
                "official_boe_html_count": sum(1 for row in regulations_rows if row["extraction_source"] == "official_boe_html"),
                "local_document_fallback_count": sum(1 for row in regulations_rows if row["extraction_source"] == "local_document_fallback"),
                "articles_emitted": len(article_rows),
                "paragraphs_emitted": len(paragraph_rows),
                "chunks_emitted": len(chunk_rows),
                "article_type_distribution": dict(sorted(article_type_distribution.items())),
                "legal_function_distribution": dict(sorted(legal_function_distribution.items())),
                "topic_tag_distribution": dict(sorted(topic_tag_distribution.items())),
                "warnings": warnings,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    print(f"Regulations processed: {len(regulations_rows)}")
    print(f"Articles emitted: {len(article_rows)}")
    print(f"Paragraphs emitted: {len(paragraph_rows)}")
    print(f"Chunks emitted: {len(chunk_rows)}")
    print(f"Warnings: {len(warnings)}")


if __name__ == "__main__":
    main()
