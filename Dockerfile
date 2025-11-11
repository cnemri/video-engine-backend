# Use official lightweight Python image
FROM python:3.12-slim

# Set working directory
WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y ffmpeg && rm -rf /var/lib/apt/lists/*

# Copy dependency files first
COPY pyproject.toml uv.lock ./
RUN pip install uv
RUN uv pip install --system -r pyproject.toml

# Note: For Cloud Run, we use Application Default Credentials (ADC).
# No need to copy service-account.json or set GOOGLE_APPLICATION_CREDENTIALS.

# Copy the rest of the application code
COPY . .

# Expose port
ENV PORT=8080
EXPOSE 8080

# Run the application
CMD uvicorn main:app --host 0.0.0.0 --port ${PORT}
