FROM registry.access.redhat.com/ubi9/python-311:latest

USER 0

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt && \
    python -m spacy download en_core_web_sm

USER 1001

COPY app.py recognizers.py pseudonymizer.py config.yaml /app/
WORKDIR /app

EXPOSE 8000

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
