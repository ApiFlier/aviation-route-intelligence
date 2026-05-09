FROM python:3.11-slim

WORKDIR /app

# Install MySQL client and build dependencies
RUN apt-get update && apt-get install -y \
    default-libmysqlclient-dev \
    build-essential \
    pkg-config \
    && rm -rf /var/lib/apt/lists/*

# Copy backend requirements and install
COPY api/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy backend code
COPY api/ .

# Copy frontend assets to static folder
# Note: Build context must be project root
COPY frontend/ ./static/

EXPOSE 8080

CMD ["gunicorn", "--bind", "0.0.0.0:8080", "--workers", "1", "--threads", "4", "--timeout", "120", "main:app"]
