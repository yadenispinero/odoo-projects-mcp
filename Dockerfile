FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY pyproject.toml ./
COPY odoo_mcp ./odoo_mcp
RUN pip install --no-cache-dir .

EXPOSE 8000
CMD ["python", "-m", "odoo_mcp"]
