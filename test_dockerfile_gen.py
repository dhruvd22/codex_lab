#!/usr/bin/env python3

from pathlib import Path
from textwrap import dedent

def generate_dockerfile():
    dockerfile = dedent("""\
    FROM python:3.11-slim

    WORKDIR /app

    COPY requirements.txt ./
    RUN pip install --no-cache-dir -r requirements.txt

    COPY . .

    EXPOSE 8000

    CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
    """)

    Path("Dockerfile.test").write_text(dockerfile, encoding="utf-8")
    print("Generated Dockerfile.test successfully")
    print("Content:")
    print(dockerfile)

if __name__ == "__main__":
    generate_dockerfile()
