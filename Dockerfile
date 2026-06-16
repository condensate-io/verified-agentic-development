FROM python:3.10-slim

WORKDIR /app

RUN apt-get update && apt-get install -y curl && rm -rf /var/lib/apt/lists/*
RUN curl -L -o opa https://openpolicyagent.org/downloads/v0.61.0/opa_linux_amd64_static && chmod +x opa && mv opa /usr/local/bin/

# Copy the entire project
COPY . /app/

# Upgrade pip and install the package with dev dependencies
RUN pip install --upgrade pip && pip install -e ".[dev]"

# Run tests by default
CMD ["pytest"]
