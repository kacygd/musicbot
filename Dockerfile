FROM python:3.10-slim
RUN apt-get update && apt-get install -y ffmpeg ca-certificates && update-ca-certificates
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY bot.py .
CMD ["python", "bot.py"]
