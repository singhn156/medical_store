FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY backend ./backend
COPY frontend ./frontend
COPY sample_invoice.csv README.md ./
ENV DATABASE_URL=sqlite:////data/dosedeck.db AI_MODE=mock DEMO_MODE=true PRESENTATION_MODE=true
VOLUME ["/data"]
EXPOSE 8000
# Use the PORT env var if provided (Render sets $PORT). Fall back to 8000.
CMD ["sh", "-lc", "uvicorn backend.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
