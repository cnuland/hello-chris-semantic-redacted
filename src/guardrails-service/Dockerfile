FROM registry.access.redhat.com/ubi9/python-311:latest

# Switch to root to install dependencies
USER 0

COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt && \
    rm /tmp/requirements.txt

# Switch to non-root user
USER 1001

COPY config.py rails.py app.py /opt/app-root/src/
WORKDIR /opt/app-root/src

EXPOSE 8001

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8001"]
