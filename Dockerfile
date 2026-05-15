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
COPY . .

# Create required directories
RUN mkdir -p cogs/archive/backups logs

# Run the bot
CMD ["python", "main.py"]
