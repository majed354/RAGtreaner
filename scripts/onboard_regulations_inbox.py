#!/usr/bin/env python3
"""مسار عام لإدخال الأنظمة الجديدة إلى قاعدة المعرفة القانونية."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sys
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import build_structured_legal_corpus as corpus


ROOT = corpus.ROOT
INBOX_DIR = ROOT / "documents" / "saudi_regulations" / "inbox"
ONBOARDED_DIR = ROOT / "documents" / "saudi_regulations" / "onboarded"
CANDIDATES_PATH = ROOT / "catalog" / "inbox_regulations_candidates.json"
CUSTOM_CATALOG_PATH = ROOT / "catalog" / "custom_regulations_catalog.json"

SUPPORTED_SUFFIXES = {".pdf", ".docx", ".txt"}
TITLE_HINT_RE = re.compile(r"^(?:النظام|لائحة|اللائحة|قواعد|ضوابط|سياسة|تنظيم)\b")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def ensure_onboarding_paths() -> None:
    INBOX_DIR.mkdir(parents=True, exist_ok=True)
    ONBOARDED_DIR.mkdir(parents=True, exist_ok=True)
    if not CUSTOM_CATALOG_PATH.exists():
        CUSTOM_CATALOG_PATH.write_text(
            json.dumps({"generated_on": _now_iso(), "entries": []}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    if not CANDIDATES_PATH.exists():
        CANDIDATES_PATH.write_text(
            json.dumps({"generated_on": _now_iso(), "entries": []}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


def _load_json_payload(path: Path) -> dict:
    if not path.exists():
        return {"entries": []}
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload.setdefault("entries", [])
    return payload


def _save_json_payload(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _stable_candidate_id(path: Path) -> str:
    stat = path.stat()
    raw = f"{path.name}|{stat.st_size}|{int(stat.st_mtime)}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]


def _sanitize_stem(stem: str) -> str:
    value = unicodedata.normalize("NFKC", stem or "")
    value = re.sub(r"[_\-]+", " ", value)
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def _guess_title(path: Path, normalized_text: str) -> str:
    for line in normalized_text.splitlines()[:40]:
        line = line.strip()
        if len(line) < 4:
            continue
        if TITLE_HINT_RE.search(line):
            return line[:220].strip(" :.-")

    stem = _sanitize_stem(path.stem)
    stem = re.sub(r"\b\d+\b", " ", stem)
    stem = re.sub(r"\s+", " ", stem).strip()
    return stem or path.stem


def _slugify_latin(text: str) -> str:
    value = unicodedata.normalize("NFKD", text or "")
    value = value.encode("ascii", "ignore").decode("ascii").lower()
    value = re.sub(r"[^a-z0-9]+", "-", value).strip("-")
    value = re.sub(r"-{2,}", "-", value)
    return value


def _suggest_slug(path: Path, title_ar: str, candidate_id: str) -> str:
    filename_slug = _slugify_latin(path.stem)
    title_slug = _slugify_latin(title_ar)
    slug = filename_slug or title_slug
    if not slug:
        slug = f"uploaded-regulation-{candidate_id[:8]}"
    return slug


def _quality_level(text_chars: int, article_count: int) -> str:
    if text_chars >= 2000 and article_count >= 3:
        return "strong"
    if text_chars >= 700 and article_count >= 1:
        return "medium"
    return "weak"


def _quality_flags(text_chars: int, article_count: int, suffix: str) -> list[str]:
    flags = []
    if suffix not in SUPPORTED_SUFFIXES:
        flags.append("unsupported_extension")
    if text_chars < 700:
        flags.append("low_text_chars")
    if article_count == 0:
        flags.append("no_articles_detected")
    if article_count == 1:
        flags.append("single_article_detected")
    return flags


def build_candidate(path: Path, existing_slugs: set[str]) -> dict:
    raw_text = corpus.extract_text(path)
    verbatim_text = corpus.prepare_verbatim_text(raw_text)
    normalized_text = corpus.normalize_text(verbatim_text)
    parsed_articles = corpus.split_articles(normalized_text) if normalized_text else []
    article_labels = [item.get("article_label", "") for item in parsed_articles[:5] if item.get("article_label")]
    candidate_id = _stable_candidate_id(path)
    title_ar = _guess_title(path, normalized_text or verbatim_text)
    suggested_slug = _suggest_slug(path, title_ar, candidate_id)
    flags = _quality_flags(len(normalized_text), len(parsed_articles), path.suffix.lower())
    if suggested_slug in existing_slugs:
        suggested_slug = f"{suggested_slug}-{candidate_id[:4]}"
        flags.append("slug_collision_adjusted")

    return {
        "candidate_id": candidate_id,
        "source_filename": path.name,
        "source_relpath": str(path.relative_to(ROOT)),
        "file_type": path.suffix.lower(),
        "suggested_title_ar": title_ar,
        "suggested_slug": suggested_slug,
        "text_chars": len(normalized_text),
        "article_count": len(parsed_articles),
        "sample_article_labels": article_labels,
        "quality_level": _quality_level(len(normalized_text), len(parsed_articles)),
        "quality_flags": flags,
        "review_status": "pending",
        "scanned_at": _now_iso(),
    }


def scan_inbox() -> int:
    ensure_onboarding_paths()
    merged_catalog = corpus.load_catalog()
    existing_slugs = {entry.get("slug", "").strip() for entry in merged_catalog if entry.get("slug")}
    candidates = []
    for path in sorted(INBOX_DIR.iterdir()):
        if not path.is_file() or path.suffix.lower() not in SUPPORTED_SUFFIXES:
            continue
        candidates.append(build_candidate(path, existing_slugs))

    payload = {
        "generated_on": _now_iso(),
        "entries": candidates,
    }
    _save_json_payload(CANDIDATES_PATH, payload)

    print(f"📥 الملفات المفحوصة في inbox: {len(candidates)}")
    for candidate in candidates:
        print(
            f"- {candidate['candidate_id']} | {candidate['source_filename']} | "
            f"slug={candidate['suggested_slug']} | quality={candidate['quality_level']} | "
            f"articles={candidate['article_count']} | chars={candidate['text_chars']}"
        )
    return 0


def list_candidates() -> int:
    ensure_onboarding_paths()
    payload = _load_json_payload(CANDIDATES_PATH)
    entries = payload.get("entries", [])
    if not entries:
        print("لا توجد ملفات مفحوصة حاليًا. شغّل scan أولًا.")
        return 0

    print(f"📋 عدد المرشحين: {len(entries)}")
    for candidate in entries:
        print(
            f"- {candidate['candidate_id']} | {candidate['source_filename']} | "
            f"title={candidate['suggested_title_ar']} | slug={candidate['suggested_slug']} | "
            f"quality={candidate['quality_level']} | status={candidate.get('review_status', 'pending')}"
        )
    return 0


def approve_candidate(candidate_id: str, *, slug: str | None, title: str | None, force: bool) -> int:
    ensure_onboarding_paths()
    candidates_payload = _load_json_payload(CANDIDATES_PATH)
    candidates = candidates_payload.get("entries", [])
    candidate = next((item for item in candidates if item.get("candidate_id") == candidate_id), None)
    if not candidate:
        raise SystemExit(f"لم يتم العثور على المرشح: {candidate_id}")

    if candidate.get("quality_level") == "weak" and not force:
        raise SystemExit("جودة الاستخراج ضعيفة. استخدم --force إذا كنت تريد اعتماده رغم ذلك.")

    source_path = ROOT / candidate["source_relpath"]
    if not source_path.exists():
        raise SystemExit(f"الملف المصدر غير موجود: {source_path}")

    merged_catalog = corpus.load_catalog()
    existing_slugs = {entry.get("slug", "").strip() for entry in merged_catalog if entry.get("slug")}

    final_slug = (slug or candidate.get("suggested_slug") or "").strip()
    if not final_slug:
        raise SystemExit("تعذر تحديد slug نهائي للملف.")
    if final_slug in existing_slugs:
        raise SystemExit(f"الـ slug مستخدم بالفعل: {final_slug}")

    final_title = (title or candidate.get("suggested_title_ar") or "").strip()
    if not final_title:
        raise SystemExit("تعذر تحديد عنوان عربي للوثيقة.")

    target_filename = f"{final_slug}{source_path.suffix.lower()}"
    target_path = ONBOARDED_DIR / target_filename
    if target_path.exists():
        raise SystemExit(f"الملف الهدف موجود بالفعل: {target_path.name}")

    shutil.move(str(source_path), str(target_path))

    custom_catalog = _load_json_payload(CUSTOM_CATALOG_PATH)
    custom_entries = custom_catalog.get("entries", [])
    custom_entries.append(
        {
            "slug": final_slug,
            "title_ar": final_title,
            "status": "قيد المراجعة المرجعية",
            "issue_date_hijri": "",
            "issue_date_gregorian": "",
            "publish_date_hijri": "",
            "publish_date_gregorian": "",
            "official_source_urls": [],
            "source_relpath": str(target_path.relative_to(ROOT)),
            "organized_filename": target_filename,
            "knowledge_filename": f"{final_slug}.txt",
            "document_scope": "uploaded_reference",
            "onboarding_metadata": {
                "candidate_id": candidate["candidate_id"],
                "source_filename": candidate["source_filename"],
                "quality_level": candidate["quality_level"],
                "quality_flags": candidate.get("quality_flags", []),
                "article_count": candidate.get("article_count", 0),
                "text_chars": candidate.get("text_chars", 0),
                "approved_on": _now_iso(),
            },
        }
    )
    custom_catalog["generated_on"] = _now_iso()
    custom_catalog["entries"] = custom_entries
    _save_json_payload(CUSTOM_CATALOG_PATH, custom_catalog)

    for item in candidates:
        if item.get("candidate_id") == candidate_id:
            item["review_status"] = "approved"
            item["approved_slug"] = final_slug
            item["approved_title_ar"] = final_title
            item["approved_on"] = _now_iso()
            item["target_relpath"] = str(target_path.relative_to(ROOT))

    candidates_payload["generated_on"] = _now_iso()
    candidates_payload["entries"] = candidates
    _save_json_payload(CANDIDATES_PATH, candidates_payload)

    print("✅ تم اعتماد الوثيقة الجديدة بنجاح")
    print(f"slug: {final_slug}")
    print(f"title: {final_title}")
    print(f"stored_at: {target_path.relative_to(ROOT)}")
    print("الخطوة التالية:")
    print("1. ./manage.sh build-legal")
    print("2. ./manage.sh ingest")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Onboarding عام للأنظمة واللوائح الجديدة")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("scan", help="فحص الملفات داخل inbox وبناء قائمة مرشحين")
    subparsers.add_parser("list", help="عرض قائمة المرشحين الحالية")

    approve_parser = subparsers.add_parser("approve", help="اعتماد ملف من المرشحين")
    approve_parser.add_argument("--id", required=True, help="candidate_id الناتج من أمر scan")
    approve_parser.add_argument("--slug", help="slug نهائي اختياري")
    approve_parser.add_argument("--title", help="عنوان عربي نهائي اختياري")
    approve_parser.add_argument("--force", action="store_true", help="اعتماد الملف حتى مع جودة استخراج ضعيفة")

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    if args.command == "scan":
        return scan_inbox()
    if args.command == "list":
        return list_candidates()
    if args.command == "approve":
        return approve_candidate(
            args.id,
            slug=args.slug,
            title=args.title,
            force=bool(args.force),
        )

    parser.error("أمر غير مدعوم")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
