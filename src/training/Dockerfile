FROM registry.access.redhat.com/ubi9/python-311:latest

WORKDIR /app

COPY src/training/ ./src/training/
COPY configs/ ./configs/
COPY data/ ./data/

RUN pip install --no-cache-dir --extra-index-url https://download.pytorch.org/whl/cpu \
    sentence-transformers==3.4.1 \
    datasets==3.2.0 \
    torch==2.5.1+cpu \
    "accelerate>=0.26.0" \
    pyyaml \
    requests

USER 1001

CMD ["python", "-m", "src.training.pipeline", "configs/training.yaml", "configs/sdg.yaml", "data/sensitivity-anchors/anchors.jsonl"]
