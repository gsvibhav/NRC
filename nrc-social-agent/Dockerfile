# Suitable for JustRunMy.App (see ARCHITECTURE.md, "Hosting & deployment").
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src ./src

RUN useradd --create-home --uid 1000 appuser
USER appuser

# All configuration is supplied at runtime via environment variables
# (see .env.example) — never baked into the image.
CMD ["python", "-m", "src.main"]
