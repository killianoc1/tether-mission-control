FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# GeoDjango needs GDAL, GEOS and PROJ at runtime.
RUN apt-get update \
    && apt-get install -y --no-install-recommends gdal-bin libgdal-dev libgeos-dev libproj-dev binutils \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN chmod +x /app/entrypoint.sh

# Run as an unprivileged user.
RUN useradd --create-home --uid 1000 app && chown -R app:app /app
USER app

EXPOSE 8000
ENTRYPOINT ["/app/entrypoint.sh"]
# Django's built-in server (as in the labs); Nginx sits in front of it.  --noreload: no file watcher needed in a container.
CMD ["python", "manage.py", "runserver", "0.0.0.0:8000", "--noreload"]
