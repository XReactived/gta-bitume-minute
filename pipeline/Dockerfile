FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg fonts-dejavu-core fontconfig \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
# Voix-off française téléchargée une fois pour toutes
RUN python -m piper.download_voices fr_FR-siwis-medium --download-dir /app/voix || echo "voix telechargee au premier lancement"
COPY gta_reels ./gta_reels
COPY config.yaml .
ENV GTA_REELS_DATA=/app/data PYTHONUNBUFFERED=1
CMD ["python", "-m", "gta_reels.main"]
