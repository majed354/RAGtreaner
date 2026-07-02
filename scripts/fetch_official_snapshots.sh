#!/bin/bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
CATALOG_PATH="$ROOT_DIR/catalog/saudi_regulations_catalog.json"
SNAPSHOT_DIR="$ROOT_DIR/data/structured/official_snapshots"
MANIFEST_FILE="$(mktemp)"
CURL_ARGS=(
  --fail
  --location
  --silent
  --show-error
  --retry 2
  --retry-delay 2
  --connect-timeout 20
  --max-time 120
  --compressed
  -A
  "Mozilla/5.0 (compatible; CodexLegalIndexer/1.0)"
)
success_count=0
failure_count=0

mkdir -p "$SNAPSHOT_DIR"

python3 - <<'PY' "$CATALOG_PATH" > "$MANIFEST_FILE"
import json
import sys
from pathlib import Path

catalog_path = Path(sys.argv[1])
entries = json.loads(catalog_path.read_text(encoding="utf-8"))["entries"]
for entry in entries:
    primary_url = entry["official_source_urls"][0] if entry.get("official_source_urls") else ""
    print(f"{entry['slug']}\t{entry['title_ar']}\t{primary_url}")
PY

while IFS=$'\t' read -r slug title url; do
  resolved_url="$url"

  if [[ "$resolved_url" == *"laws.boe.gov.sa"* ]] && ([[ "$resolved_url" == *"/Viewer/"* ]] || [[ "$resolved_url" == *"/Search?"* ]]); then
    encoded_title="$(python3 - <<'PY' "$title"
import sys
import urllib.parse
print(urllib.parse.quote(sys.argv[1]))
PY
)"
    search_url="https://laws.boe.gov.sa/BoeLaws/Laws/Search/?LanguageId=1&Query=${encoded_title}&SearchTypeId=3"
    tmp_search="$(mktemp)"
    curl "${CURL_ARGS[@]}" "$search_url" -o "$tmp_search" || true
    discovered_url="$(python3 - <<'PY' "$tmp_search" "$title"
import html
import re
import sys
from pathlib import Path

content = Path(sys.argv[1]).read_text(encoding="utf-8", errors="ignore")
title_ar = sys.argv[2].strip()
pattern = re.compile(
    r'<a class="result-keyword-title" href="(?P<law_href>[^"]+)">(?P<title>[^<]+)</a>.*?'
    r'<a href="(?P<search_href>[^"]*SearchDetails[^"]*)">المزيد من نتائج البحث</a>',
    re.S,
)
for match in pattern.finditer(content):
    result_title = html.unescape(match.group("title")).strip()
    if result_title == title_ar:
        href = html.unescape(match.group("search_href"))
        if href.startswith("/"):
            href = "https://laws.boe.gov.sa" + href
        print(href)
        break
PY
)"
    rm -f "$tmp_search"
    if [[ -n "${discovered_url:-}" ]]; then
      resolved_url="$discovered_url"
    fi
  fi

  target="$SNAPSHOT_DIR/$slug.html"
  echo "Fetching $slug"
  if ! curl "${CURL_ARGS[@]}" "$resolved_url" -o "$target"; then
    echo "Failed $slug" >&2
    failure_count=$((failure_count + 1))
  else
    success_count=$((success_count + 1))
  fi
done < "$MANIFEST_FILE"

rm -f "$MANIFEST_FILE"
echo "Completed. success=$success_count failure=$failure_count"
