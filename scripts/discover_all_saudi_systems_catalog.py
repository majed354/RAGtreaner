"""اكتشاف جميع الأنظمة السعودية الحالية من بوابة هيئة الخبراء وتحديث الكتالوج الرسمي."""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import html
import json
import re
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote, urljoin

ROOT = Path(__file__).resolve().parent.parent
CATALOG_PATH = ROOT / "catalog" / "saudi_regulations_catalog.json"
REPORT_PATH = ROOT / "catalog" / "all_systems_discovery_report.json"
SNAPSHOT_DIR = ROOT / "data" / "structured" / "official_snapshots"
STATE_PATH = ROOT / "data" / "structured" / "official_sync_state.json"
FOLDERS_URL = "https://laws.boe.gov.sa/BoeLaws/Laws/Folders/1"
BASE_URL = "https://laws.boe.gov.sa"
USER_AGENT = "Mozilla/5.0 (compatible; CodexLegalIndexer/1.0)"
LAW_LINK_RE = re.compile(
    r'<li><a href="(?P<href>/BoeLaws/Laws/LawDetails/[^"]+/1)"><i class="fas fa-angle-left"></i>(?P<title>[^<]+)</a></li>'
)
RESULT_ITEM_RE = re.compile(
    r'<a class="result-keyword-title" href="(?P<law_href>[^"]+)">(?P<title>[^<]+)</a>.*?'
    r'<a href="(?P<search_href>[^"]*SearchDetails[^"]*)">المزيد من نتائج البحث</a>',
    re.S,
)
PAGE_TITLE_RE = re.compile(r'<h1 class="page-title">(.*?)</h1>', re.S)
LABEL_VALUE_RE_TEMPLATE = r"<label[^>]*>\s*{label}\s*</label>\s*<span[^>]*>\s*(.*?)\s*</span>"
SYSTEM_TITLE_RE = re.compile(
    r'^(?:النظام(?:\s+الأساسي)?|نظام|قانون\s*\(نظام\)|القانون\s*\(النظام\)|القانون\s*"النظام")'
)
CURRENT_STATUS_MARKERS = ("ساري", "نافذ")
INACTIVE_STATUS_MARKERS = ("لاغي", "ملغي", "ملغى", "منسوخ", "منتهي")
GENERIC_PAGE_TITLES = {"التفاصيل", "details", "عذراً، لقد حدث خطأ", "عذرا، لقد حدث خطأ"}
INVALID_PAGE_MARKERS = (
    "العنصر المطلوب غير موجود بالنظام",
    "عذراً، لقد حدث خطأ",
    "عذرا، لقد حدث خطأ",
)
LOW_QUALITY_SLUG_RE = re.compile(r"^(?:altfasyl(?:-[a-f0-9]+)?|adhra-lqd-hdth-khta)$")
LEGACY_SLUG_OVERRIDES = {
    "النظام الأساسي للحكم": "basic-law-of-governance",
    "نظام الأحوال الشخصية": "personal-status-law",
    "نظام الإثبات": "law-of-evidence",
    "نظام المعاملات المدنية": "civil-transactions-law",
    "نظام الإجراءات الجزائية": "criminal-procedure-law",
    "نظام المرافعات الشرعية": "law-of-sharia-procedure",
    "نظام التنفيذ": "execution-law",
    "نظام مكافحة غسل الأموال": "anti-money-laundering-law",
    "نظام الشركات": "companies-law",
    "نظام العمل": "labor-law",
    "نظام المنافسات والمشتريات الحكومية": "government-tenders-and-procurement-law",
    "نظام المنافسات و المشتريات الحكومية": "government-tenders-and-procurement-law",
    "نظام الوساطة العقارية": "real-estate-brokerage-law",
    "نظام الاتصالات وتقنية المعلومات": "communications-and-information-technology-law",
    "نظام التعاملات الإلكترونية": "electronic-transactions-law",
    "نظام التجارة الإلكترونية": "e-commerce-law",
    "نظام حماية البيانات الشخصية": "personal-data-protection-law",
    "نظام مكافحة الغش التجاري": "commercial-fraud-law",
    "نظام سلامة المنتجات": "product-safety-law",
    "نظام مكافحة جرائم المعلوماتية": "anti-cybercrime-law",
    "نظام الجامعات": "universities-law",
    "نظام الحماية من الإيذاء": "protection-from-abuse-law",
    "نظام حماية حقوق المؤلف": "copyright-law",
    "نظام حماية المبلغين والشهود والخبراء والضحايا": "whistleblowers-witnesses-experts-and-victims-protection-law",
}
ARABIC_TRANSLITERATION = {
    "ا": "a",
    "أ": "a",
    "إ": "i",
    "آ": "a",
    "ب": "b",
    "ت": "t",
    "ث": "th",
    "ج": "j",
    "ح": "h",
    "خ": "kh",
    "د": "d",
    "ذ": "dh",
    "ر": "r",
    "ز": "z",
    "س": "s",
    "ش": "sh",
    "ص": "s",
    "ض": "d",
    "ط": "t",
    "ظ": "z",
    "ع": "a",
    "غ": "gh",
    "ف": "f",
    "ق": "q",
    "ك": "k",
    "ل": "l",
    "م": "m",
    "ن": "n",
    "ه": "h",
    "ة": "h",
    "و": "w",
    "ؤ": "w",
    "ي": "y",
    "ى": "a",
    "ئ": "y",
    "ء": "a",
}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_spaces(text: str) -> str:
    return " ".join(html.unescape(text or "").split()).strip()


def strip_tags(fragment: str) -> str:
    text = re.sub(r"<[^>]+>", "", fragment or "")
    return normalize_spaces(text)


def load_existing_catalog(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"verified_on": "", "verification_scope_note": "", "entries": []}
    return json.loads(path.read_text(encoding="utf-8"))


def load_existing_state() -> dict[str, Any]:
    if not STATE_PATH.exists():
        return {"entries": {}}
    try:
        payload = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {"entries": {}}
    payload.setdefault("entries", {})
    return payload


def save_state(payload: dict[str, Any]) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def curl_fetch_text(url: str) -> str:
    result = subprocess.run(
        [
            "curl",
            "--fail",
            "--location",
            "--silent",
            "--show-error",
            "--retry",
            "2",
            "--retry-delay",
            "2",
            "--connect-timeout",
            "20",
            "--max-time",
            "120",
            "--compressed",
            "-A",
            USER_AGENT,
            url,
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout


def looks_like_system_title(title: str) -> bool:
    return bool(SYSTEM_TITLE_RE.match(normalize_spaces(title)))


def extract_label_value(page_html: str, label_text: str) -> str:
    pattern = re.compile(LABEL_VALUE_RE_TEMPLATE.format(label=re.escape(label_text)), re.S)
    match = pattern.search(page_html)
    return strip_tags(match.group(1)) if match else ""


def extract_gregorian_iso(label_value: str) -> str:
    match = re.search(r"(\d{2})/(\d{2})/(\d{4})", label_value or "")
    if not match:
        return ""
    day, month, year = match.groups()
    return f"{year}-{month}-{day}"


def normalize_status(status: str) -> str:
    return normalize_spaces(status).replace(" ", "")


def is_current_status(status: str) -> bool:
    normalized = normalize_status(status)
    if not normalized:
        return True
    if any(marker in normalized for marker in INACTIVE_STATUS_MARKERS):
        return False
    if any(marker in normalized for marker in CURRENT_STATUS_MARKERS):
        return True
    return True


def law_id_from_url(url: str) -> str:
    match = re.search(r"/LawDetails/([^/]+)/1", url)
    return match.group(1) if match else hashlib.sha1(url.encode("utf-8")).hexdigest()[:8]


def transliterate_to_slug(text: str) -> str:
    pieces: list[str] = []
    for char in normalize_spaces(text).lower():
        if char.isascii() and char.isalnum():
            pieces.append(char)
            continue
        mapped = ARABIC_TRANSLITERATION.get(char)
        if mapped:
            pieces.append(mapped)
        else:
            pieces.append("-")
    slug = "".join(pieces)
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    slug = re.sub(r"-{2,}", "-", slug).strip("-")
    slug = re.sub(r"^(al-)?nizam-", "", slug)
    slug = re.sub(r"^(al-)?alnizam-", "", slug)
    slug = re.sub(r"^(al-)?alqanwn-", "", slug)
    return slug[:96].strip("-")


def build_slug(
    title_ar: str,
    detail_url: str,
    existing_by_title: dict[str, dict[str, Any]],
    existing_by_url: dict[str, dict[str, Any]],
    used_slugs: set[str],
) -> str:
    normalized_title = normalize_spaces(title_ar)

    def reuse_existing_slug(candidate_slug: str) -> str:
        slug = candidate_slug.strip()
        if not slug or LOW_QUALITY_SLUG_RE.fullmatch(slug):
            return ""
        used_slugs.add(slug)
        return slug

    if normalized_title in LEGACY_SLUG_OVERRIDES:
        slug = LEGACY_SLUG_OVERRIDES[normalized_title]
        used_slugs.add(slug)
        return slug
    if detail_url in existing_by_url:
        slug = reuse_existing_slug(existing_by_url[detail_url].get("slug", ""))
        if slug:
            return slug
    if normalized_title in existing_by_title:
        slug = reuse_existing_slug(existing_by_title[normalized_title].get("slug", ""))
        if slug:
            return slug

    law_id = law_id_from_url(detail_url)
    base = transliterate_to_slug(normalized_title)
    if len(base) < 8:
        base = f"saudi-system-{law_id[:8]}"
    slug = base
    if slug in used_slugs:
        slug = f"{base}-{law_id[:8]}"
    counter = 2
    while slug in used_slugs:
        slug = f"{base}-{counter}"
        counter += 1
    used_slugs.add(slug)
    return slug


def parse_folder_candidates(page_html: str) -> list[dict[str, str]]:
    seen: dict[str, dict[str, str]] = {}
    for match in LAW_LINK_RE.finditer(page_html):
        href = match.group("href")
        title_ar = normalize_spaces(match.group("title"))
        if not looks_like_system_title(title_ar):
            continue
        detail_url = urljoin(BASE_URL, href)
        seen[detail_url] = {
            "title_ar": title_ar,
            "detail_url": detail_url,
        }
    return sorted(seen.values(), key=lambda item: item["title_ar"])


def preferred_generated_filename(previous_value: str, slug: str, suffix: str) -> str:
    candidate = (previous_value or "").strip()
    if not candidate:
        return f"{slug}.{suffix}"
    stem = Path(candidate).stem
    if LOW_QUALITY_SLUG_RE.fullmatch(stem):
        return f"{slug}.{suffix}"
    return candidate


def search_boe_url_by_title(title_ar: str) -> str | None:
    query = quote(title_ar)
    search_url = f"{BASE_URL}/BoeLaws/Laws/Search/?LanguageId=1&Query={query}&SearchTypeId=3"
    page_html = curl_fetch_text(search_url)
    normalized_title = normalize_spaces(title_ar)
    for match in RESULT_ITEM_RE.finditer(page_html):
        result_title = normalize_spaces(match.group("title"))
        if result_title != normalized_title:
            continue
        law_href = html.unescape(match.group("law_href"))
        search_href = html.unescape(match.group("search_href"))
        if law_href:
            return urljoin(BASE_URL, law_href)
        if search_href:
            return urljoin(BASE_URL, search_href)
    return None


def is_invalid_page(page_html: str, page_title: str) -> bool:
    normalized_title = normalize_spaces(page_title).lower()
    if normalized_title in GENERIC_PAGE_TITLES:
        return True
    return any(marker in page_html for marker in INVALID_PAGE_MARKERS)


def fetch_detail_metadata(candidate: dict[str, str]) -> dict[str, Any]:
    detail_url = candidate["detail_url"]
    page_html = curl_fetch_text(detail_url)
    title_match = PAGE_TITLE_RE.search(page_html)
    page_title = strip_tags(title_match.group(1)) if title_match else candidate["title_ar"]
    if is_invalid_page(page_html, page_title):
        discovered_url = search_boe_url_by_title(candidate["title_ar"])
        if discovered_url and discovered_url != detail_url:
            detail_url = discovered_url
            page_html = curl_fetch_text(detail_url)
            title_match = PAGE_TITLE_RE.search(page_html)
            page_title = strip_tags(title_match.group(1)) if title_match else candidate["title_ar"]
    unresolved = is_invalid_page(page_html, page_title)
    if page_title.lower() in GENERIC_PAGE_TITLES:
        page_title = candidate["title_ar"]
    status = extract_label_value(page_html, "الحالة")
    issue_date_hijri = extract_label_value(page_html, "تاريخ الإصدار")
    publish_date_hijri = extract_label_value(page_html, "تاريخ النشر")
    return {
        "title_ar": page_title or candidate["title_ar"],
        "detail_url": detail_url,
        "status": status,
        "issue_date_hijri": issue_date_hijri,
        "issue_date_gregorian": extract_gregorian_iso(issue_date_hijri),
        "publish_date_hijri": publish_date_hijri,
        "publish_date_gregorian": extract_gregorian_iso(publish_date_hijri),
        "page_html": page_html,
        "is_current": is_current_status(status),
        "unresolved": unresolved,
    }


def write_snapshot(slug: str, page_html: str) -> str:
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    snapshot_path = SNAPSHOT_DIR / f"{slug}.html"
    snapshot_path.write_text(page_html, encoding="utf-8")
    return str(snapshot_path.relative_to(ROOT))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--include-non-current", action="store_true")
    args = parser.parse_args()

    existing_catalog = load_existing_catalog(CATALOG_PATH)
    existing_entries = existing_catalog.get("entries", [])
    existing_by_title = {
        normalize_spaces(entry.get("title_ar", "")): entry
        for entry in existing_entries
        if entry.get("title_ar")
    }
    existing_by_url = {
        (entry.get("official_source_urls") or [""])[0]: entry
        for entry in existing_entries
        if entry.get("official_source_urls")
    }

    folder_html = curl_fetch_text(FOLDERS_URL)
    candidates = parse_folder_candidates(folder_html)
    if args.limit > 0:
        candidates = candidates[: args.limit]

    discovered: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []
    inactive: list[dict[str, str]] = []
    unresolved: list[dict[str, str]] = []

    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, args.workers)) as executor:
        future_map = {
            executor.submit(fetch_detail_metadata, candidate): candidate
            for candidate in candidates
        }
        for future in concurrent.futures.as_completed(future_map):
            candidate = future_map[future]
            try:
                payload = future.result()
            except Exception as exc:
                failures.append(
                    {
                        "title_ar": candidate["title_ar"],
                        "detail_url": candidate["detail_url"],
                        "error": str(exc),
                    }
                )
                continue

            if payload.get("unresolved"):
                unresolved.append(
                    {
                        "title_ar": candidate["title_ar"],
                        "detail_url": candidate["detail_url"],
                    }
                )
                continue

            if not payload["is_current"] and not args.include_non_current:
                inactive.append(
                    {
                        "title_ar": payload["title_ar"],
                        "detail_url": payload["detail_url"],
                        "status": payload["status"],
                    }
                )
                continue

            discovered.append(payload)

    used_slugs = set()
    final_entries: list[dict[str, Any]] = []
    state_entries: dict[str, Any] = {}
    fetched_at = now_iso()

    for payload in sorted(discovered, key=lambda item: item["title_ar"]):
        slug = build_slug(
            payload["title_ar"],
            payload["detail_url"],
            existing_by_title,
            existing_by_url,
            used_slugs,
        )
        previous = existing_by_url.get(payload["detail_url"]) or existing_by_title.get(normalize_spaces(payload["title_ar"])) or {}
        snapshot_relpath = write_snapshot(slug, payload["page_html"])
        content_sha256 = hashlib.sha256(payload["page_html"].encode("utf-8")).hexdigest()

        entry = {
            "slug": slug,
            "title_ar": payload["title_ar"],
            "status": payload["status"] or previous.get("status", "قيد المراجعة"),
            "issue_date_hijri": payload["issue_date_hijri"] or previous.get("issue_date_hijri", ""),
            "issue_date_gregorian": payload["issue_date_gregorian"] or previous.get("issue_date_gregorian", ""),
            "publish_date_hijri": payload["publish_date_hijri"] or previous.get("publish_date_hijri", ""),
            "publish_date_gregorian": payload["publish_date_gregorian"] or previous.get("publish_date_gregorian", ""),
            "official_source_urls": [payload["detail_url"]],
            "organized_filename": preferred_generated_filename(previous.get("organized_filename", ""), slug, "pdf"),
            "knowledge_filename": preferred_generated_filename(previous.get("knowledge_filename", ""), slug, "txt"),
            "document_scope": previous.get("document_scope", "system_only"),
        }
        for optional_key in ("local_source_relpath", "source_relpath"):
            if previous.get(optional_key):
                entry[optional_key] = previous[optional_key]
        final_entries.append(entry)
        state_entries[slug] = {
            "slug": slug,
            "title_ar": payload["title_ar"],
            "catalog_source_url": payload["detail_url"],
            "resolved_url": payload["detail_url"],
            "snapshot_relpath": snapshot_relpath,
            "last_checked_at": fetched_at,
            "last_success_at": fetched_at,
            "last_status": "updated",
            "content_sha256": content_sha256,
            "etag": "",
            "last_modified": "",
            "last_modified_parsed": None,
            "last_error": "",
        }

    backup_path = CATALOG_PATH.with_suffix(".backup.json")
    if CATALOG_PATH.exists():
        shutil.copy2(CATALOG_PATH, backup_path)

    catalog_payload = {
        "verified_on": datetime.now(timezone.utc).date().isoformat(),
        "verification_scope_note": (
            "تم توليد هذا الكتالوج آليًا من صفحة الوثائق النظامية في بوابة هيئة الخبراء "
            "مع حصر العناوين التي تندرج تحت الأنظمة فقط، ثم التحقق من حالة كل وثيقة من صفحة النظام نفسها "
            "واستبعاد غير الساري افتراضيًا."
        ),
        "entries": final_entries,
    }
    CATALOG_PATH.write_text(json.dumps(catalog_payload, ensure_ascii=False, indent=2), encoding="utf-8")

    state_payload = load_existing_state()
    state_payload["entries"] = state_entries
    state_payload["last_run_started_at"] = fetched_at
    state_payload["last_run_finished_at"] = fetched_at
    state_payload["last_run_summary"] = {
        "checked_entries": len(candidates),
        "fetched_entries": len(discovered),
        "changed_entries": len(discovered),
        "unchanged_entries": 0,
        "failed_entries": len(failures),
        "inactive_entries_skipped": len(inactive),
        "unresolved_entries_skipped": len(unresolved),
        "build_triggered": False,
        "built_successfully": False,
        "catalog_discovered": True,
    }
    save_state(state_payload)

    active_slugs = {entry["slug"] for entry in final_entries}
    if SNAPSHOT_DIR.exists():
        for snapshot_path in SNAPSHOT_DIR.glob("*.html"):
            if snapshot_path.stem not in active_slugs:
                snapshot_path.unlink(missing_ok=True)

    report_payload = {
        "generated_at": fetched_at,
        "folder_url": FOLDERS_URL,
        "total_candidates": len(candidates),
        "written_entries": len(final_entries),
        "inactive_skipped": len(inactive),
        "unresolved_skipped": len(unresolved),
        "failed_count": len(failures),
        "backup_catalog": str(backup_path.relative_to(ROOT)) if backup_path.exists() else "",
        "inactive_examples": inactive[:25],
        "unresolved_examples": unresolved[:25],
        "failure_examples": failures[:25],
    }
    REPORT_PATH.write_text(json.dumps(report_payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps(report_payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
