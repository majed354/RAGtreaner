"""تنظيم ملفات الأنظمة السعودية في مجلدات واضحة."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CATALOG_PATH = ROOT / "catalog" / "saudi_regulations_catalog.json"
OFFICIAL_DIR = ROOT / "documents" / "saudi_regulations" / "official_latest"
PENDING_DIR = ROOT / "documents" / "saudi_regulations" / "pending_review"
PENDING_INVENTORY_PATH = ROOT / "catalog" / "pending_review_inventory.json"
REPORT_PATH = ROOT / "catalog" / "organization_report.json"
ARCHIVE_ROOT = ROOT / "archive" / "raw_unsorted" / "الأنظمة والقوانين"
ALLOWED_SUFFIXES = {".pdf", ".docx", ".txt"}
LAW_KEYWORDS = ("نظام", "النظام", "لائحة", "لائحة", "اللائحة", "قانون")


def load_catalog() -> list[dict]:
    payload = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    return payload["entries"]


def reset_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    for child in path.iterdir():
        if child.is_file():
            child.unlink()


def unique_destination(path: Path) -> Path:
    if not path.exists():
        return path

    counter = 2
    while True:
        candidate = path.with_name(f"{path.stem}__{counter}{path.suffix}")
        if not candidate.exists():
            return candidate
        counter += 1


def looks_like_regulation_file(path: Path) -> bool:
    if path.suffix.lower() not in ALLOWED_SUFFIXES:
        return False
    return any(keyword in path.name for keyword in LAW_KEYWORDS)


def main() -> None:
    entries = load_catalog()
    reset_directory(OFFICIAL_DIR)
    reset_directory(PENDING_DIR)

    matched_sources: set[Path] = set()
    copied_verified = []
    missing_verified = []

    for entry in entries:
        src = ROOT / entry["local_source_relpath"]
        dst = OFFICIAL_DIR / entry["organized_filename"]
        if not src.exists():
            missing_verified.append(
                {
                    "slug": entry["slug"],
                    "expected_source": str(src.relative_to(ROOT)),
                }
            )
            continue

        matched_sources.add(src.resolve())
        shutil.copy2(src, dst)
        copied_verified.append(
            {
                "slug": entry["slug"],
                "title_ar": entry["title_ar"],
                "source": str(src.relative_to(ROOT)),
                "target": str(dst.relative_to(ROOT)),
            }
        )

    pending_inventory = []
    for path in sorted(ARCHIVE_ROOT.rglob("*")):
        if not path.is_file():
            continue
        if path.resolve() in matched_sources:
            continue
        if not looks_like_regulation_file(path):
            continue

        dst = unique_destination(PENDING_DIR / path.name)
        shutil.copy2(path, dst)
        pending_inventory.append(
            {
                "source": str(path.relative_to(ROOT)),
                "target": str(dst.relative_to(ROOT)),
            }
        )

    PENDING_INVENTORY_PATH.write_text(
        json.dumps(pending_inventory, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    REPORT_PATH.write_text(
        json.dumps(
            {
                "verified_files_copied": copied_verified,
                "verified_missing": missing_verified,
                "pending_review_count": len(pending_inventory),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    print(f"Verified copied: {len(copied_verified)}")
    print(f"Pending copied: {len(pending_inventory)}")
    if missing_verified:
        print(f"Missing verified sources: {len(missing_verified)}")


if __name__ == "__main__":
    main()
