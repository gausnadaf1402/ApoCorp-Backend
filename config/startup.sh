#!/bin/bash
playwright install-deps chromium   # installs OS libs only, fast, idempotent
python manage.py migrate
gunicorn config.wsgi:application --bind 0.0.0.0:8000