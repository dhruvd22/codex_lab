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
