FROM python:3.12-slim
RUN pip install --no-cache-dir pandas pyarrow boto3 psycopg2-binary
WORKDIR /app
COPY load.py /app/load.py
CMD ["python", "/app/load.py"]
