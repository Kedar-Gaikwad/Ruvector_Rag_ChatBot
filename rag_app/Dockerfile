# Python base image
FROM python:3.11-slim

# Install system dependencies for scientific libraries (numpy, pandas)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy dependency requirements
COPY backend/requirements.txt .

# Install dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Pre-download local zero-cost models during build to ensure fast startups
RUN python -c "from sentence_transformers import SentenceTransformer, CrossEncoder; \
               SentenceTransformer('all-MiniLM-L6-v2'); \
               CrossEncoder('cross-encoder/ms-marco-MiniLM-L-6-v2')"

# Copy application source code
COPY backend/ ./backend/
COPY frontend/ ./frontend/

WORKDIR /app/backend

# Expose FastAPI port
EXPOSE 8000

ENV RUVECTOR_URL=postgresql://ruvector:ruvector@ruvector:5432/ruvector_db
ENV EMBEDDING_PROVIDER=local
ENV LLM_PROVIDER=openai

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
