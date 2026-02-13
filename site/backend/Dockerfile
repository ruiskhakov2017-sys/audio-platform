FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1
ENV PORT=8000

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && pip install --no-cache-dir Pillow

COPY . .
# Нормализация переводов строк (CRLF → LF) и права на start.sh
RUN sed -i 's/\r$//' start.sh 2>/dev/null || true && chmod +x start.sh
# Статика собирается при сборке образа (при старте контейнера нет DB/S3 — только migrate + gunicorn)
RUN python manage.py collectstatic --noinput 2>/dev/null || true

EXPOSE 8000

CMD ["sh", "start.sh"]
