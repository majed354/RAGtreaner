"""مزامنة النصوص الرسمية للأنظمة السعودية وبناء الطبقة المنظمة تلقائياً."""

from __future__ import annotations

import asyncio
import hashlib
import html
import json
import logging
import re
import subprocess
import sys
import tempfile
import urllib.parse
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Dict, Optional, Tuple

from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CATALOG_PATH = PROJECT_ROOT / "catalog" / "saudi_regulations_catalog.json"
SNAPSHOT_DIR = PROJECT_ROOT / "data" / "structured" / "official_snapshots"
STATE_PATH = PROJECT_ROOT / "data" / "structured" / "official_sync_state.json"
BUILD_SCRIPT_PATH = PROJECT_ROOT / "scripts" / "build_structured_legal_corpus.py"
CHUNKS_PATH = PROJECT_ROOT / "data" / "structured" / "chunks.jsonl"
USER_AGENT = "Mozilla/5.0 (compatible; CodexLegalIndexer/1.0)"

RESULT_ITEM_RE = re.compile(
    r'<a class="result-keyword-title" href="(?P<law_href>[^"]+)">(?P<title>[^<]+)</a>.*?'
    r'<a href="(?P<search_href>[^"]*SearchDetails[^"]*)">المزيد من نتائج البحث</a>',
    re.S,
)
LABEL_VALUE_RE_TEMPLATE = r"<label[^>]*>\s*{label}\s*</label>\s*<span[^>]*>\s*(.*?)\s*</span>"
CURRENT_STATUS_MARKERS = ("ساري", "نافذ")
INACTIVE_STATUS_MARKERS = ("لاغي", "ملغي", "ملغى", "منسوخ", "منتهي")


@dataclass
class EntrySyncResult:
    slug: str
    status: str
    reason: str
    changed: bool
    source_url: str
    message: str = ""


@dataclass
class OfficialSyncRunResult:
    checked_entries: int
    fetched_entries: int
    changed_entries: int
    unchanged_entries: int
    failed_entries: int
    inactive_removed_entries: int
    build_triggered: bool
    built_successfully: bool
    results: list[EntrySyncResult]


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _to_iso(value: Optional[datetime]) -> Optional[str]:
    return value.isoformat() if value else None


def _parse_iso(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except Exception:
        return None


def _parse_http_date(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    try:
        parsed = parsedate_to_datetime(value)
    except Exception:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat()


def _load_catalog_payload() -> dict:
    if not CATALOG_PATH.exists():
        return {"entries": []}
    payload = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    payload.setdefault("entries", [])
    return payload


def _save_catalog_payload(payload: dict) -> None:
    CATALOG_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _load_catalog_entries() -> list[dict]:
    return _load_catalog_payload()["entries"]


def _normalize_title(text: str) -> str:
    return " ".join(html.unescape(text or "").split()).strip()


def _extract_label_value(page_html: str, label_text: str) -> str:
    pattern = re.compile(LABEL_VALUE_RE_TEMPLATE.format(label=re.escape(label_text)), re.S)
    match = pattern.search(page_html or "")
    if not match:
        return ""
    value = re.sub(r"<[^>]+>", "", match.group(1))
    return _normalize_title(value)


def _normalize_status(status: str) -> str:
    return _normalize_title(status).replace(" ", "")


def _is_current_status(status: str) -> bool:
    normalized = _normalize_status(status)
    if not normalized:
        return True
    if any(marker in normalized for marker in INACTIVE_STATUS_MARKERS):
        return False
    if any(marker in normalized for marker in CURRENT_STATUS_MARKERS):
        return True
    return True


def _extract_page_status(page_html: str) -> str:
    return _extract_label_value(page_html, "الحالة")


class OfficialSyncService:
    def __init__(self):
        self._lock = asyncio.Lock()

    def _snapshot_path(self, slug: str) -> Path:
        return SNAPSHOT_DIR / f"{slug}.html"

    def _remove_catalog_entry(self, slug: str) -> bool:
        payload = _load_catalog_payload()
        entries = payload.get("entries", [])
        filtered_entries = [entry for entry in entries if entry.get("slug") != slug]
        if len(filtered_entries) == len(entries):
            return False
        payload["entries"] = filtered_entries
        _save_catalog_payload(payload)
        return True

    def _load_state(self) -> dict:
        if not STATE_PATH.exists():
            return {"entries": {}}
        try:
            payload = json.loads(STATE_PATH.read_text(encoding="utf-8"))
            payload.setdefault("entries", {})
            return payload
        except Exception as e:
            logger.warning(f"تعذر قراءة حالة المزامنة الرسمية السابقة: {e}")
            return {"entries": {}}

    def _save_state(self, state: dict) -> None:
        STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        STATE_PATH.write_text(
            json.dumps(state, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _purge_inactive_entry(self, slug: str, state: dict) -> bool:
        catalog_changed = self._remove_catalog_entry(slug)
        snapshot_path = self._snapshot_path(slug)
        snapshot_existed = snapshot_path.exists()
        snapshot_path.unlink(missing_ok=True)
        state.setdefault("entries", {}).pop(slug, None)
        return catalog_changed or snapshot_existed

    def _preferred_source_url(self, entry: dict) -> str:
        urls = entry.get("official_source_urls") or []
        for marker in ("/LawDetails/", "SearchDetails", "/Viewer/"):
            for url in urls:
                if marker in url:
                    return url
        return urls[0] if urls else ""

    def _curl_fetch(
        self,
        url: str,
        output_path: Path,
        request_headers: Optional[Dict[str, str]] = None,
    ) -> Tuple[int, str, Dict[str, str]]:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        request_headers = request_headers or {}
        with tempfile.NamedTemporaryFile(delete=False) as headers_handle:
            headers_path = Path(headers_handle.name)

        command = [
            "curl",
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
            str(settings.official_sync_request_timeout_seconds),
            "--compressed",
            "-A",
            USER_AGENT,
            "-D",
            str(headers_path),
            "-o",
            str(output_path),
            "-w",
            "\n%{http_code}",
        ]
        for header_name, header_value in request_headers.items():
            command.extend(["-H", f"{header_name}: {header_value}"])
        command.append(url)

        result = subprocess.run(
            command,
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
        )
        stdout = result.stdout.strip()
        status_code = 0
        if stdout:
            try:
                status_code = int(stdout.splitlines()[-1].strip())
            except ValueError:
                status_code = 0

        headers_text = headers_path.read_text(encoding="utf-8", errors="ignore") if headers_path.exists() else ""
        parsed_headers = self._parse_response_headers(headers_text)
        headers_path.unlink(missing_ok=True)

        if result.returncode != 0 and status_code == 0:
            message = (result.stderr or result.stdout or "").strip()
            raise RuntimeError(message or f"curl failed for {url}")

        return status_code, (result.stderr or "").strip(), parsed_headers

    def _parse_response_headers(self, headers_text: str) -> Dict[str, str]:
        blocks = [block.strip() for block in re.split(r"\r?\n\r?\n", headers_text) if block.strip()]
        if not blocks:
            return {}

        selected_block = ""
        for block in reversed(blocks):
            if block.startswith("HTTP/"):
                selected_block = block
                break
        if not selected_block:
            return {}

        lines = [line.strip() for line in selected_block.splitlines() if line.strip()]
        headers: Dict[str, str] = {}
        if lines:
            headers[":status"] = lines[0]
        for line in lines[1:]:
            if ":" not in line:
                continue
            key, value = line.split(":", 1)
            headers[key.strip().lower()] = value.strip()
        return headers

    def _search_boe_url_by_title(self, title_ar: str) -> Optional[str]:
        encoded_title = urllib.parse.quote(title_ar)
        search_url = f"https://laws.boe.gov.sa/BoeLaws/Laws/Search/?LanguageId=1&Query={encoded_title}&SearchTypeId=3"

        with tempfile.NamedTemporaryFile(delete=False, suffix=".html") as handle:
            tmp_path = Path(handle.name)

        try:
            self._curl_fetch(search_url, tmp_path)
            content = tmp_path.read_text(encoding="utf-8", errors="ignore")
        finally:
            tmp_path.unlink(missing_ok=True)

        normalized_title = _normalize_title(title_ar)
        for match in RESULT_ITEM_RE.finditer(content):
            result_title = _normalize_title(match.group("title"))
            if result_title != normalized_title:
                continue

            law_href = html.unescape(match.group("law_href"))
            search_href = html.unescape(match.group("search_href"))
            if law_href:
                return urllib.parse.urljoin("https://laws.boe.gov.sa", law_href)
            if search_href:
                return urllib.parse.urljoin("https://laws.boe.gov.sa", search_href)
        return None

    def _entry_needs_sync(self, entry: dict, state_entry: Optional[dict], force: bool) -> Tuple[bool, str]:
        if force:
            return True, "forced"

        snapshot_path = self._snapshot_path(entry["slug"])
        preferred_url = self._preferred_source_url(entry)
        if not snapshot_path.exists():
            return True, "missing_snapshot"
        if not state_entry:
            return True, "never_synced"
        if not state_entry.get("last_success_at"):
            return True, "never_synced"
        if state_entry.get("catalog_source_url") != preferred_url:
            return True, "catalog_source_changed"

        last_success_at = _parse_iso(state_entry.get("last_success_at"))
        if last_success_at is None:
            return True, "invalid_last_success_at"

        refresh_after = timedelta(seconds=max(60, settings.official_sync_interval_seconds))
        if _utc_now() - last_success_at >= refresh_after:
            return True, "scheduled_refresh"

        return False, "up_to_date"

    def _run_build_script(self) -> Tuple[bool, str]:
        result = subprocess.run(
            [sys.executable, str(BUILD_SCRIPT_PATH)],
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
        )
        output = (result.stdout or "") + ("\n" + result.stderr if result.stderr else "")
        return result.returncode == 0, output.strip()

    def get_status(self) -> dict:
        catalog_entries = _load_catalog_entries()
        state = self._load_state()
        entry_states = state.get("entries", {})

        synced_entries = 0
        pending_entries = 0
        for entry in catalog_entries:
            slug = entry["slug"]
            snapshot_exists = self._snapshot_path(slug).exists()
            state_entry = entry_states.get(slug, {})
            if snapshot_exists and state_entry.get("last_success_at"):
                synced_entries += 1
            else:
                pending_entries += 1

        return {
            "catalog_entries": len(catalog_entries),
            "synced_entries": synced_entries,
            "pending_entries": pending_entries,
            "last_run_started_at": state.get("last_run_started_at"),
            "last_run_finished_at": state.get("last_run_finished_at"),
            "last_build_at": state.get("last_build_at"),
            "last_build_status": state.get("last_build_status"),
            "last_run_summary": state.get("last_run_summary", {}),
        }

    def _sync_entry(self, entry: dict, state_entry: Optional[dict], reason: str) -> EntrySyncResult:
        slug = entry["slug"]
        title_ar = entry["title_ar"]
        snapshot_path = self._snapshot_path(slug)
        snapshot_exists_before = snapshot_path.exists()
        preferred_url = self._preferred_source_url(entry)

        request_headers: Dict[str, str] = {}
        if state_entry and state_entry.get("etag"):
            request_headers["If-None-Match"] = state_entry["etag"]
        if state_entry and state_entry.get("last_modified"):
            request_headers["If-Modified-Since"] = state_entry["last_modified"]

        candidate_urls = []
        for candidate in [preferred_url, state_entry.get("resolved_url") if state_entry else None]:
            if candidate and candidate not in candidate_urls:
                candidate_urls.append(candidate)

        fallback_url: Optional[str] = None
        if not candidate_urls:
            fallback_url = self._search_boe_url_by_title(title_ar)
            if fallback_url:
                candidate_urls.append(fallback_url)

        errors = []

        for index, url in enumerate(candidate_urls, start=1):
            try:
                with tempfile.NamedTemporaryFile(delete=False, suffix=".html") as handle:
                    tmp_output = Path(handle.name)

                status_code, stderr_text, headers = self._curl_fetch(
                    url,
                    tmp_output,
                    request_headers=request_headers if index == 1 else {},
                )
                html_text = tmp_output.read_text(encoding="utf-8", errors="ignore") if tmp_output.exists() else ""
                tmp_output.unlink(missing_ok=True)
            except Exception as e:
                errors.append(f"{url}: {e}")
                continue

            page_status = _extract_page_status(html_text)
            if status_code == 304 and snapshot_exists_before:
                content_hash = state_entry.get("content_sha256") if state_entry else ""
                return EntrySyncResult(
                    slug=slug,
                    status="not_modified",
                    reason=reason,
                    changed=False,
                    source_url=url,
                    message=content_hash,
                )

            if status_code < 200 or status_code >= 300:
                errors.append(f"{url}: HTTP {status_code} {stderr_text}".strip())
                continue

            if page_status and not _is_current_status(page_status):
                return EntrySyncResult(
                    slug=slug,
                    status="inactive",
                    reason=reason,
                    changed=True,
                    source_url=url,
                    message=json.dumps(
                        {
                            "detected_status": page_status,
                            "resolved_url": url,
                        },
                        ensure_ascii=False,
                    ),
                )

            if 'class="article_item' not in html_text:
                discovered_url = self._search_boe_url_by_title(title_ar)
                if discovered_url and discovered_url not in candidate_urls:
                    candidate_urls.append(discovered_url)
                errors.append(f"{url}: no article items found")
                continue

            snapshot_path.parent.mkdir(parents=True, exist_ok=True)
            snapshot_path.write_text(html_text, encoding="utf-8")
            content_sha256 = hashlib.sha256(html_text.encode("utf-8")).hexdigest()
            previous_hash = (state_entry or {}).get("content_sha256")
            changed = (not snapshot_exists_before) or content_sha256 != previous_hash

            entry_state = {
                "slug": slug,
                "title_ar": title_ar,
                "catalog_source_url": preferred_url,
                "resolved_url": url,
                "snapshot_relpath": str(snapshot_path.relative_to(PROJECT_ROOT)),
                "last_checked_at": _to_iso(_utc_now()),
                "last_success_at": _to_iso(_utc_now()),
                "last_status": "updated" if changed else "not_modified",
                "content_sha256": content_sha256,
                "etag": headers.get("etag", ""),
                "last_modified": headers.get("last-modified", ""),
                "last_modified_parsed": _parse_http_date(headers.get("last-modified")),
                "last_error": "",
            }

            return EntrySyncResult(
                slug=slug,
                status="updated" if changed else "not_modified",
                reason=reason,
                changed=changed,
                source_url=url,
                message=json.dumps(entry_state, ensure_ascii=False),
            )

        return EntrySyncResult(
            slug=slug,
            status="failed",
            reason=reason,
            changed=False,
            source_url=preferred_url,
            message=" | ".join(errors) if errors else "no candidate URLs available",
        )

    async def sync(self, force: bool = False) -> OfficialSyncRunResult:
        async with self._lock:
            entries = _load_catalog_entries()
            state = self._load_state()
            entry_states = state.setdefault("entries", {})
            state["last_run_started_at"] = _to_iso(_utc_now())

            results: list[EntrySyncResult] = []
            fetched_entries = 0
            changed_entries = 0
            unchanged_entries = 0
            failed_entries = 0
            inactive_removed_entries = 0

            for entry in entries:
                slug = entry["slug"]
                state_entry = entry_states.get(slug)
                needs_sync, reason = self._entry_needs_sync(entry, state_entry, force)
                if not needs_sync:
                    results.append(
                        EntrySyncResult(
                            slug=slug,
                            status="skipped",
                            reason=reason,
                            changed=False,
                            source_url=self._preferred_source_url(entry),
                        )
                    )
                    unchanged_entries += 1
                    continue

                logger.info("🌐 مزامنة النظام الرسمي: %s (%s)", slug, reason)
                result = await asyncio.to_thread(self._sync_entry, entry, state_entry, reason)
                results.append(result)

                if result.status == "failed":
                    failed_entries += 1
                    entry_state = entry_states.setdefault(slug, {})
                    entry_state.update(
                        {
                            "slug": slug,
                            "title_ar": entry["title_ar"],
                            "catalog_source_url": self._preferred_source_url(entry),
                            "last_checked_at": _to_iso(_utc_now()),
                            "last_status": "failed",
                            "last_error": result.message,
                        }
                    )
                    continue

                if result.status == "inactive":
                    fetched_entries += 1
                    changed_entries += 1
                    inactive_removed_entries += 1
                    self._purge_inactive_entry(slug, state)
                    continue

                fetched_entries += 1
                if result.changed:
                    changed_entries += 1
                else:
                    unchanged_entries += 1

                try:
                    updated_state = json.loads(result.message) if result.message.startswith("{") else {}
                except Exception:
                    updated_state = {}
                if updated_state:
                    entry_states[slug] = updated_state
                else:
                    entry_state = entry_states.setdefault(slug, {})
                    entry_state.update(
                        {
                            "slug": slug,
                            "title_ar": entry["title_ar"],
                            "catalog_source_url": self._preferred_source_url(entry),
                            "resolved_url": result.source_url,
                            "last_checked_at": _to_iso(_utc_now()),
                            "last_success_at": _to_iso(_utc_now()),
                            "last_status": result.status,
                            "last_error": "",
                        }
                    )

            build_needed = changed_entries > 0 or not CHUNKS_PATH.exists()
            built_successfully = False
            if build_needed:
                logger.info("🧱 تم رصد تحديث رسمي؛ جارٍ إعادة بناء الطبقة المنظمة")
                built_successfully, build_output = await asyncio.to_thread(self._run_build_script)
                state["last_build_at"] = _to_iso(_utc_now())
                state["last_build_status"] = "success" if built_successfully else "failed"
                state["last_build_output"] = build_output
                if not built_successfully:
                    logger.error("❌ فشل إعادة بناء الطبقة المنظمة بعد المزامنة الرسمية")
            else:
                state["last_build_output"] = state.get("last_build_output", "")

            state["last_run_finished_at"] = _to_iso(_utc_now())
            state["last_run_summary"] = {
                "checked_entries": len(entries),
                "fetched_entries": fetched_entries,
                "changed_entries": changed_entries,
                "unchanged_entries": unchanged_entries,
                "failed_entries": failed_entries,
                "inactive_removed_entries": inactive_removed_entries,
                "build_triggered": build_needed,
                "built_successfully": built_successfully,
            }
            self._save_state(state)

            return OfficialSyncRunResult(
                checked_entries=len(entries),
                fetched_entries=fetched_entries,
                changed_entries=changed_entries,
                unchanged_entries=unchanged_entries,
                failed_entries=failed_entries,
                inactive_removed_entries=inactive_removed_entries,
                build_triggered=build_needed,
                built_successfully=built_successfully,
                results=results,
            )


_official_sync_service: Optional[OfficialSyncService] = None


def get_official_sync_service() -> OfficialSyncService:
    global _official_sync_service
    if _official_sync_service is None:
        _official_sync_service = OfficialSyncService()
    return _official_sync_service


async def run_official_sync_once(force: bool = False) -> OfficialSyncRunResult:
    service = get_official_sync_service()
    return await service.sync(force=force)


async def _main() -> int:
    force = "--force" in sys.argv
    result = await run_official_sync_once(force=force)
    print(
        json.dumps(
            {
                "checked_entries": result.checked_entries,
                "fetched_entries": result.fetched_entries,
                "changed_entries": result.changed_entries,
                "unchanged_entries": result.unchanged_entries,
                "failed_entries": result.failed_entries,
                "inactive_removed_entries": result.inactive_removed_entries,
                "build_triggered": result.build_triggered,
                "built_successfully": result.built_successfully,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if result.failed_entries == 0 else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
