# Back-office RESILIENCE-ALPHA — authentification visage + mot de passe.
#
# OpenCV headless : pas de dépendance GUI/X11 dans l'image.
#
# Les modèles (YuNet 232 Ko + SFace 37 Mo) ne sont pas versionnés. Deux cas :
#   * ils ont été pré-téléchargés sur le poste (`python fetch_models.py`) :
#     `COPY . .` les apporte, la construction fonctionne SANS réseau ;
#   * ils sont absents : le Dockerfile les télécharge et vérifie leur SHA-256.
# Dans les deux cas, l'empreinte est contrôlée et l'échec est explicite — jamais
# un modèle différent de celui validé, jamais un téléchargement au démarrage.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    BO_DB_PATH=/data/backoffice.db \
    BO_FACE_MODEL_DIR=/app/models

WORKDIR /app

# libglib2.0-0 est exigé par le wheel OpenCV même en version headless.
RUN apt-get update \
 && apt-get install -y --no-install-recommends libglib2.0-0 ca-certificates curl \
 && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN set -eu; \
    mkdir -p /app/models; \
    YUNET=/app/models/face_detection_yunet_2023mar.onnx; \
    SFACE=/app/models/face_recognition_sface_2021dec.onnx; \
    if [ ! -s "$YUNET" ]; then \
      curl -fsSL -o "$YUNET" \
        https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx; \
    fi; \
    if [ ! -s "$SFACE" ]; then \
      curl -fsSL -o "$SFACE" \
        https://github.com/opencv/opencv_zoo/raw/main/models/face_recognition_sface/face_recognition_sface_2021dec.onnx; \
    fi; \
    echo "8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4  $YUNET" | sha256sum -c -; \
    echo "0ba9fbfa01b5270c96627c4ef784da859931e02f04419c829e83484087c34e79  $SFACE" | sha256sum -c -; \
    python -c "import cv2; cv2.FaceDetectorYN.create('$YUNET','',(320,320))"; \
    echo "modèles faciaux chargés et vérifiés"

RUN mkdir -p /data
VOLUME ["/data"]

EXPOSE 8086
HEALTHCHECK --interval=10s --timeout=3s --retries=6 \
  CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8086/healthz',timeout=2)"

CMD ["uvicorn", "main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8086"]
