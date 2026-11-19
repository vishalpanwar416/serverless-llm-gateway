# Image for running locally. Runs the lambda handler through scripts/local_server.py.
# In AWS the same src/ code runs on the normal python3.12 Lambda runtime.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src ./src
COPY scripts ./scripts

EXPOSE 8080
CMD ["sh", "-c", "python scripts/bootstrap_local.py && python scripts/local_server.py"]
