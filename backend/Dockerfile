FROM python:3.13-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py global_markets.py alerts.py futures.py domestic_futures.py basis.py ./

EXPOSE 9090
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "9090"]
