FROM registry.access.redhat.com/ubi9/python-311:latest

USER 0

COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt && \
    rm /tmp/requirements.txt

USER 1001

WORKDIR /app
COPY app.py config.py /app/
COPY config/ /app/config/

EXPOSE 8003

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8003"]
