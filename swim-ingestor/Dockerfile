# Build context must be the repo root (set in docker-compose.swim.yml).
# This allows copying api/Data/swim_schema.sql into the image so apply_schema.py
# can run inside the container without an external volume mount.
FROM python:3.11-slim

WORKDIR /app

COPY swim-ingestor/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Copy sidecar source files
COPY swim-ingestor/ ./

# Copy the schema file so apply_schema.py and db.py can find it inside Docker
COPY api/Data/swim_schema.sql ./swim_schema.sql

CMD ["python", "main.py"]
