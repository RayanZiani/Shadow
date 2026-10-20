# Boutique RESILIENCE-ALPHA — site vitrine + administration.
#
# La base est PostgreSQL (service `db-postgres` de la pile). psycopg[binary]
# embarque libpq : aucun paquet système supplémentaire n'est nécessaire.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8087
HEALTHCHECK --interval=10s --timeout=3s --retries=6 \
  CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8087/healthz',timeout=2)"

CMD ["uvicorn", "main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8087"]
