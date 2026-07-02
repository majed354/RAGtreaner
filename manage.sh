#!/bin/bash
# ═══════════════════════════════════════════════
# 🔧 أوامر إدارة البوت
# الاستخدام: ./manage.sh [أمر]
# ═══════════════════════════════════════════════

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
ENV_FILE="$ROOT_DIR/.env"

read_env_value() {
  local key="$1"
  local default_value="$2"
  if [[ -f "$ENV_FILE" ]]; then
    local value
    value="$(grep -E "^${key}=" "$ENV_FILE" | tail -n 1 | cut -d'=' -f2-)"
    if [[ -n "$value" ]]; then
      printf '%s\n' "$value"
      return
    fi
  fi
  printf '%s\n' "$default_value"
}

SERVER_PORT="$(read_env_value server_port 8000)"
INSTANCE_LABEL="$(read_env_value instance_label legal-consultation-bot)"

case "$1" in
  start)
    echo "🚀 تشغيل البوت..."
    docker compose up -d --build
    ;;
  stop)
    echo "🛑 إيقاف البوت..."
    docker compose down
    ;;
  restart)
    echo "🔄 إعادة تشغيل..."
    docker compose restart
    ;;
  logs)
    echo "📋 عرض السجلات..."
    docker compose logs -f --tail=100
    ;;
  ingest)
    echo "📚 تجهيز قاعدة المعرفة يدوياً..."
    docker compose exec bot python -m app.rag.ingest
    ;;
  sync-official)
    echo "🌐 جلب اللقطات الرسمية للأنظمة..."
    docker compose run --rm bot python -m app.official_sync
    ;;
  build-legal)
    echo "🧱 بناء الفهرس القانوني المنظم..."
    python3 ./scripts/build_structured_legal_corpus.py
    ;;
  onboard-scan)
    echo "📥 فحص ملفات inbox واقتراح الأنظمة الجديدة..."
    python3 ./scripts/onboard_regulations_inbox.py scan
    ;;
  onboard-list)
    echo "📋 عرض المرشحين الجدد..."
    python3 ./scripts/onboard_regulations_inbox.py list
    ;;
  onboard-approve)
    echo "✅ اعتماد ملف جديد من inbox..."
    shift
    python3 ./scripts/onboard_regulations_inbox.py approve "$@"
    ;;
  status)
    echo "📊 حالة البوت..."
    echo "🏷️ النسخة: $INSTANCE_LABEL"
    echo "🔌 المنفذ: $SERVER_PORT"
    docker compose ps
    echo ""
    curl -s "http://localhost:${SERVER_PORT}/health" | python3 -m json.tool 2>/dev/null || echo "⚠️ الخادم لا يستجيب"
    ;;
  shell)
    echo "🐚 دخول الحاوية..."
    docker compose exec bot bash
    ;;
  update)
    echo "⬆️ تحديث من GitHub..."
    git pull
    docker compose up -d --build
    echo "✅ تم التحديث"
    ;;
  *)
    echo "═══════════════════════════════════════"
    echo " ⚖️ إدارة بوت الاستشارات القانونية"
    echo "═══════════════════════════════════════"
    echo ""
    echo "الأوامر المتاحة:"
    echo "  start    — تشغيل البوت"
    echo "  stop     — إيقاف البوت"
    echo "  restart  — إعادة تشغيل"
    echo "  logs     — عرض السجلات"
    echo "  ingest   — تجهيز قاعدة المعرفة يدوياً"
    echo "  sync-official — جلب الصفحات الرسمية"
    echo "  build-legal   — بناء JSON/CSV/TXT القانوني"
    echo "  onboard-scan  — فحص ملفات inbox واقتراحها"
    echo "  onboard-list  — عرض المرشحين الجدد"
    echo "  onboard-approve --id <candidate_id> [--slug ...] [--title ...] [--force]"
    echo "  status   — حالة البوت"
    echo "  shell    — دخول الحاوية"
    echo "  update   — تحديث من GitHub"
    echo ""
    ;;
esac
