# Usa imagem leve do Python 3.11
FROM python:3.11-slim

# Define diretório de trabalho
WORKDIR /app

# Copia e instala dependências
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copia todo o projeto
COPY . .

# Render passa a variável PORT automaticamente
ENV PYTHONUNBUFFERED=1

# Comando principal: roda o servidor FastAPI
CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}"]
