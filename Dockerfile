FROM python:3.11-slim

WORKDIR /app

# Suppress pip version check warnings
ENV PIP_DISABLE_PIP_VERSION_CHECK=1

# Copy requirements and install script first for better caching
COPY requirements.txt .
COPY install_dependencies.sh .

# Make install script executable and run it
RUN chmod +x install_dependencies.sh && ./install_dependencies.sh

# Copy application code
COPY . .

# Create required directories
RUN mkdir -p cogs/archive/backups logs

# Run the bot
CMD ["python", "main.py"]
