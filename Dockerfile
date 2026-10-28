FROM python:3.14-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    LIBRARY_PATH=/library

WORKDIR /app
COPY src ./src
COPY LICENSE ./

USER 1000:1000
EXPOSE 8080

CMD ["python", "-m", "src.server", "--host", "0.0.0.0", "--port", "8080"]
