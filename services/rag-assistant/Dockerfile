# Assistant RAG — index construit AU BUILD, jamais au runtime.
#
# Le contexte de construction est la RACINE du dépôt (voir docker-compose.yml) :
# c'est ce qui permet de copier `docs/` et `README.md` dans l'image et d'y
# construire l'index. L'assistant démarre donc avec son index déjà prêt :
# aucun téléchargement, aucune indexation au premier appel.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    RAG_INDEX=/app/index.json

WORKDIR /app

COPY services/rag-assistant/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY services/rag-assistant/ .
COPY README.md /app/corpus/README.md
COPY TODO.md /app/corpus/TODO.md
COPY docs/ /app/corpus/docs/
COPY services/backoffice/README.md /app/corpus/services-backoffice.md
COPY services/shop-api/README.md /app/corpus/services-shop.md
COPY hardware/README.md /app/corpus/hardware.md

RUN python build_index.py --corpus /app/corpus --out /app/index.json \
 && python -c "import json; d=json.load(open('/app/index.json')); \
    assert d['documents'] >= 5 and len(d['chunks']) > 100, d['documents']; \
    print('index RAG vérifié :', d['documents'], 'documents')"

EXPOSE 8091
HEALTHCHECK --interval=10s --timeout=3s --retries=6 \
  CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8091/healthz',timeout=2)"

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8091"]
