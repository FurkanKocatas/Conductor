FROM python:3.12-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
COPY ui ./ui
COPY migrations ./migrations
COPY alembic.ini migrate_all.py ./
EXPOSE 8000
# Cloud Run injects $PORT and the container MUST listen on it; locally the
# compose default (8000) applies. Shell form so the variable expands.
# Migrations run out of band (compose `migrate` service / CI / deploy step), not
# here — so multiple Cloud Run instances never race to migrate on startup.
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
