FROM node:22-slim

# Install system dependencies
RUN apt-get update && apt-get install -y \
    git \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install Claude Code CLI
RUN npm install -g @anthropic-ai/claude-code

# Create app directory
WORKDIR /app

# Copy runner files
COPY runner.sh BACKLOG.md CLAUDE.md ./
COPY .env* ./
RUN chmod +x runner.sh

# Create directories for state and logs
RUN mkdir -p .state logs

# Mount the target project as a volume
VOLUME ["/project"]

ENTRYPOINT ["./runner.sh"]
