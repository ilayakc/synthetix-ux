# Taban imaj, `requirements.txt` icindeki `playwright` surumuyle birebir
# eslesir (v1.49.0) ve Chromium + tum sistem bagimliliklarini onceden,
# Microsoft tarafindan derlenmis/dogrulanmis sekilde icerir. Bu, `pip
# install playwright && playwright install --with-deps chromium`
# adimlarini (ve olasi surum uyumsuzlugu / eksik apt bagimliligi riskini)
# production image'inda tamamen ortadan kaldirir.
FROM mcr.microsoft.com/playwright/python:v1.49.0-jammy

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

EXPOSE 8100

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8100"]
