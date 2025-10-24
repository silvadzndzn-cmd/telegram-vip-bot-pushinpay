# Usa Python 3.11 (evita os erros de build no 3.13)
FROM python:3.11-slim

# Deixa o Python “limpinho”
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Dependências de compilação mínimas (caso algum pacote precise)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential curl ca-certificates \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Instala as libs do projeto
COPY requirements.txt ./
RUN pip install --upgrade pip setuptools wheel \
    && pip install --no-cache-dir -r requirements.txt

# Copia o restante do código
COPY . .

# Comando de start do bot
CMD ["python", "main.py"]
