# Playwright Python image version MUST match playwright== in requirements.txt
FROM mcr.microsoft.com/playwright/python:v1.60.0-jammy

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Persist journal and state on a mounted volume at /app/data
ENV PYTHONUNBUFFERED=1

CMD ["python", "main.py"]
