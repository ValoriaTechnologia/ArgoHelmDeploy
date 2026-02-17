FROM python:3.12-slim

RUN apt-get update \
  && apt-get install -y --no-install-recommends ca-certificates \
  && rm -rf /var/lib/apt/lists/*

  COPY main.py /main.py

  ENTRYPOINT ["python", "/main.py"]
