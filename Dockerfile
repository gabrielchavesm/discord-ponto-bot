FROM python:3.11-slim

WORKDIR /app

# Installing system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Copy only requirements first (cache layer)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY bot.py database.py ./

# Do NOT copy the .env file - use GitHub Actions secrets.
# Do not copy data/use volumes

# Execute bot
CMD ["python", "bot.py"]