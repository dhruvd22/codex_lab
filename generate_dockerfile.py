#!/usr/bin/env python3
"""Generate the multi-stage Dockerfile for The Coding Conductor service."""

from pathlib import Path
from textwrap import dedent
import sys

DOCKERFILE_TEMPLATE = dedent("""\
# syntax=docker/dockerfile:1

FROM node:20-bullseye-slim AS ui-build
WORKDIR /ui
COPY projectplanner/ui/package*.json ./
RUN npm install
COPY projectplanner/ui/ ./
ENV NEXT_PUBLIC_API_URL=""
RUN npm run build

FROM python:3.11-slim AS runtime
WORKDIR /app
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
COPY --from=ui-build /ui/out ./projectplanner/ui/out
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
""")


def generate_dockerfile() -> bool:
    """Write the Dockerfile to disk."""

    try:
        Path("Dockerfile").write_text(DOCKERFILE_TEMPLATE, encoding="utf-8")
        return True
    except OSError as exc:
        print(f"Error generating Dockerfile: {exc}")
        return False


def main() -> None:
    """Entry point for the generator script."""

    print("Generating Dockerfile...")
    if not generate_dockerfile():
        sys.exit(1)

    print("\nDockerfile content:")
    print("-" * 40)
    print(DOCKERFILE_TEMPLATE)
    print("-" * 40)
    print("Dockerfile generation completed successfully.")


if __name__ == "__main__":
    main()
