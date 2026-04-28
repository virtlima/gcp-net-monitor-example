FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY ncc_spoke_monitor.py .

# Cloud Run Jobs expect a non-root user
RUN useradd -m appuser
USER appuser

ENTRYPOINT ["python3", "ncc_spoke_monitor.py"]
