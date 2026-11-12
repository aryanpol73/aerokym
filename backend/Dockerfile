FROM python:3.12-slim

WORKDIR /app
# Install dependencies first so code edits do not invalidate this layer.
COPY pyproject.toml ./
COPY aerokym/__init__.py aerokym/__init__.py
RUN pip install --no-cache-dir .
COPY aerokym aerokym
RUN pip install --no-cache-dir --no-deps .

CMD ["python", "-m", "aerokym.db.migrate"]
