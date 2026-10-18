FROM python:3.11-slim

WORKDIR /app

# Instalar dependencias del sistema básicas para compilación numérica
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY ./app /app

# Arranca FastAPI con recarga automática al editar código
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]