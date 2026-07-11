FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PROPOSAL_TOOL_DATA_DIR=/data
RUN apt-get update && apt-get install -y --no-install-recommends libreoffice-core libreoffice-writer libreoffice-impress libreoffice-calc tesseract-ocr && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
COPY run.py ./run.py
EXPOSE 8765
CMD ["python", "run.py"]

