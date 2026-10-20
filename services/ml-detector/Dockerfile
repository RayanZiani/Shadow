FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
# `--extra-index-url` : `torch==2.14.1+cpu` n'existe que sur l'index CPU. Sans
# lui, pip télécharge le wheel CUDA (~2,5 Go) ou échoue à résoudre la version.
RUN pip install --no-cache-dir \
        --extra-index-url https://download.pytorch.org/whl/cpu \
        -r requirements.txt
COPY . .

# Entraînement à la construction si aucun artefact n'est présent.
# Pourquoi ici et pas à l'exécution : le sujet interdit explicitement
# d'entraîner en direct. Le modèle est donc figé dans l'image, ce qui garantit
# que tous les postes de l'équipe utilisent exactement le même.
#
# Pour un modèle calibré sur le trafic réel (recommandé pour la soutenance),
# entraînez hors ligne puis reconstruisez :
#     python capture_baseline.py --seconds 300
#     python train.py --from-capture baseline.jsonl
#     docker compose build ml-detector
#
# Le test porte sur les QUATRE artefacts, pas seulement sur threshold.json :
# celui-ci est versionné (il documente le seuil et les métriques citées dans le
# dossier), alors que scaler.joblib / isolation_forest.joblib / autoencoder.pt
# sont ignorés par git. Tester threshold.json seul produisait donc une image
# sans modèle : le détecteur démarrait en `degraded` et e2e_demo.py échouait.
RUN if [ ! -f threshold.json ] || [ ! -f scaler.joblib ] \
        || [ ! -f isolation_forest.joblib ] || [ ! -f autoencoder.pt ]; then \
        echo "Artefacts incomplets : entraînement (repli synthétique si pas de capture)" && \
        python train.py --epochs 400 ; \
    else \
        echo "Artefacts présents : modèle figé" ; \
    fi

# Le chargement est VÉRIFIÉ à la construction. Un artefact produit par une
# version plus récente de scikit-learn ou de torch se recharge mal (ou pas) :
# mieux vaut un build rouge le 21/10 qu'un détecteur `degraded` le jour de la
# soutenance. `train.py --check` charge les trois artefacts et le seuil.
RUN python train.py --check

EXPOSE 8083
CMD ["uvicorn", "detector:app", "--host", "0.0.0.0", "--port", "8083"]
