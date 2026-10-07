# MeasuringTempo collector image.
# Build:  docker build -t tempo-collector .
# Run:    docker run --env-file .env -v "$PWD/data:/app/data" -v "$HOME/.kalshi:/keys:ro" tempo-collector
#         (set KALSHI_KEY_PATH=/keys/<your-key-file> in .env for the container)
FROM python:3.12-slim

WORKDIR /app

# Dependencies first: this layer only rebuilds when requirements.txt changes,
# not on every code edit.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY tempo/ tempo/
COPY watchlist.json .

# Logs straight out, no buffering: `docker logs` shows lines as they happen.
ENV PYTHONUNBUFFERED=1

CMD ["python", "-m", "tempo.collector"]
