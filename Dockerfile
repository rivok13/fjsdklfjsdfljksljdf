FROM python:3.11-slim

WORKDIR /app

# Установка системных зависимостей (если нужны)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Копируем и устанавливаем Python-зависимости
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Копируем код бота
COPY . .

# Запускаем бота
CMD ["python", "main.py"]