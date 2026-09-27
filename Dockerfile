FROM python:3.11-slim

WORKDIR /workspace

# Install poetry
RUN pip install --no-cache-dir poetry==1.8.3

# Copy project files
COPY pyproject.toml poetry.lock* ./

# Configure poetry to not create a virtual environment inside the container and install dependencies
RUN poetry config virtualenvs.create false && poetry install --no-interaction --no-ansi --no-root

# Pre-download the models so they are cached in the Docker image
RUN python -c "from sentence_transformers import SentenceTransformer, CrossEncoder; SentenceTransformer('all-MiniLM-L6-v2'); CrossEncoder('cross-encoder/ms-marco-MiniLM-L-6-v2')"

# Copy the application code
COPY . .

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
