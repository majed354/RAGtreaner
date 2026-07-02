FROM python:3.12-slim

WORKDIR /app

# متطلبات النظام
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    poppler-utils \
    && rm -rf /var/lib/apt/lists/*

# تثبيت المكتبات
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# نسخ الكود
COPY . .

# إنشاء المجلدات
RUN mkdir -p /app/data/chromadb /app/documents

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
