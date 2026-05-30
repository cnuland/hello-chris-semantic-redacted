FROM registry.access.redhat.com/ubi9/python-311:latest

USER 0

COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir --extra-index-url https://download.pytorch.org/whl/cpu \
    -r /tmp/requirements.txt \
    torch==2.5.1+cpu && \
    rm /tmp/requirements.txt

USER 1001

WORKDIR /app
COPY classifier.py __init__.py app.py config.yaml /app/

COPY anchors.jsonl /app/anchors.jsonl

RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('cnuland/semantic-routing-sensitivity')"

ENV TRANSFORMERS_OFFLINE=1 HF_HUB_OFFLINE=1

EXPOSE 8002

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8002"]
