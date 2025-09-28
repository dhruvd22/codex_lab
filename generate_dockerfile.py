#!/usr/bin/env python3
"""
Script to generate Dockerfile for the FastAPI Koyeb application.
This script can be run locally or in CI/CD pipelines.
"""

from pathlib import Path
from textwrap import dedent
import sys

def generate_dockerfile():
    """Generate a Dockerfile for the FastAPI application."""
    dockerfile = dedent("""\
    FROM python:3.11-slim

    WORKDIR /app

    COPY requirements.txt ./
    RUN pip install --no-cache-dir -r requirements.txt

    COPY . .

    EXPOSE 8000

    CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
    """)

    try:
        Path("Dockerfile").write_text(dockerfile, encoding="utf-8")
        print("✅ Generated Dockerfile successfully")
        return True
    except Exception as e:
        print(f"❌ Error generating Dockerfile: {e}")
        return False

def main():
    """Main function to run the Dockerfile generation."""
    print("🐳 Generating Dockerfile...")
    
    if not generate_dockerfile():
        sys.exit(1)
    
    print("\n📄 Generated Dockerfile content:")
    print("-" * 40)
    try:
        with open("Dockerfile", "r", encoding="utf-8") as f:
            print(f.read())
    except Exception as e:
        print(f"❌ Error reading generated Dockerfile: {e}")
        sys.exit(1)
    
    print("-" * 40)
    print("🎉 Dockerfile generation completed successfully!")

if __name__ == "__main__":
    main()
