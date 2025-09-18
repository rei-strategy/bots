FROM mcr.microsoft.com/playwright/python:latest
RUN playwright install --with-deps


# If your bot uses Selenium/Chrome, uncomment next lines:
# RUN apt-get update && apt-get install -y --no-install-recommends \
#     chromium chromium-driver fonts-liberation wget ca-certificates \
#   && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Copy your full project (web UI + bot code)
COPY . /app

ENV PYTHONUNBUFFERED=1
# optional but explicit: tell webbot.py where the code is
ENV BOT_DIR=/app

EXPOSE 8000
# Serve Flask app "webbot:app" on port 8000 with gunicorn
CMD ["gunicorn", "-w", "2", "-k", "gthread", "-b", "0.0.0.0:8000", "webbot:app"]
