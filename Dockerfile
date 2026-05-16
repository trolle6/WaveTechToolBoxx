FROM python:3.12-slim

WORKDIR /app

# FFmpeg required for TTS playback (MP3 decode); disnake[voice] handles Opus/DAVE
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements and install script first for better caching
COPY requirements.txt .
COPY install_dependencies.sh .

RUN chmod +x install_dependencies.sh && ./install_dependencies.sh

# Copy application code
COPY docker-entrypoint.sh .
COPY . .

# Create required directories
RUN mkdir -p cogs/archive/backups logs \
    && chmod +x docker-entrypoint.sh

ENV GIT_BRANCH=master \
    GIT_UPDATE=true \
    PIP_INSTALL_ON_START=true

ENTRYPOINT ["/app/docker-entrypoint.sh"]
CMD ["python", "main.py"]
