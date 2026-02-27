# Use the official Playwright image (has browsers pre-installed)
FROM mcr.microsoft.com/playwright/python:v1.41.0-jammy

# Install FFmpeg for video merging
RUN apt-get update && apt-get install -y ffmpeg

# Set work directory
WORKDIR /app

# Copy files
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the code
COPY . .

# Open the port for Render (Render needs a port to stay alive)
EXPOSE 10000

# Command to run the bot
CMD ["python", "bot.py"]
