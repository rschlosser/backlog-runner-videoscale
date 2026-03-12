FROM python:3.12-slim

# Install system dependencies + Node.js for Claude Code CLI
RUN apt-get update && apt-get install -y \
    git \
    curl \
    && curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get install -y nodejs \
    && rm -rf /var/lib/apt/lists/*

# Install Claude Code CLI + deployment CLIs
RUN npm install -g @anthropic-ai/claude-code vercel @railway/cli

# Create app directory
WORKDIR /app

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy bot code
COPY bot/ ./bot/
COPY entrypoint.sh .

# Create directories
RUN mkdir -p logs .sessions

# Mount the target project as a volume
VOLUME ["/project"]

CMD ["./entrypoint.sh"]
