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
# Migrations run out of band (compose `migrate` service / CI / deploy step), not
# here — so multiple Cloud Run instances never race to migrate on startup.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
