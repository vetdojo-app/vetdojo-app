# VetDojo production deployment

Target: Ubuntu 24.04 LTS at `app.vetdojo.com`.

## Packages

```bash
sudo apt update
sudo apt upgrade -y
sudo apt install -y python3-venv python3-pip nginx redis-server certbot python3-certbot-nginx
```

## Application

Place the project at `/srv/vetdojo`, then:

```bash
cd /srv/vetdojo/web_app
python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements.txt
```

Create `/srv/vetdojo/web_app/.env` with permissions `600`. Required production values:

```env
DJANGO_SECRET_KEY=generate-a-long-random-value
DJANGO_DEBUG=false
DJANGO_ALLOWED_HOSTS=app.vetdojo.com
CSRF_TRUSTED_ORIGINS=https://app.vetdojo.com
USE_SQLITE=false
DATABASE_URL=postgresql://...
OPENAI_API_KEY=...
CELERY_BROKER_URL=redis://127.0.0.1:6379/0
CELERY_RESULT_BACKEND=redis://127.0.0.1:6379/0
DJANGO_SECURE_SSL_REDIRECT=false
DJANGO_SECURE_HSTS_SECONDS=0
DJANGO_SECURE_HSTS_INCLUDE_SUBDOMAINS=false
DJANGO_SECURE_HSTS_PRELOAD=false
```

Before enabling HSTS, verify HTTPS and every required subdomain. Never commit `.env`.

## Database and static assets

Back up the production database before the first migration, then:

```bash
cd /srv/vetdojo/web_app
.venv/bin/python manage.py check
.venv/bin/python manage.py migrate
.venv/bin/python manage.py collectstatic --noinput
.venv/bin/python manage.py createsuperuser
```

Migration `0007_shared_source_schema` updates the shared RAG uniqueness rule and active-only match function. It preserves existing source and chunk rows.

## Services

```bash
sudo cp deploy/systemd/vetdojo-gunicorn.service /etc/systemd/system/
sudo cp deploy/systemd/vetdojo-celery.service /etc/systemd/system/
sudo cp deploy/nginx/app.vetdojo.com.conf /etc/nginx/sites-available/app.vetdojo.com
sudo ln -s /etc/nginx/sites-available/app.vetdojo.com /etc/nginx/sites-enabled/app.vetdojo.com
sudo rm -f /etc/nginx/sites-enabled/default
sudo systemctl daemon-reload
sudo systemctl enable --now redis-server vetdojo-gunicorn vetdojo-celery
sudo nginx -t
sudo systemctl reload nginx
```

## HTTPS

After DNS resolves to the Elastic IP and HTTP works:

```bash
sudo certbot --nginx -d app.vetdojo.com
sudo certbot renew --dry-run
```

Then change the four HTTPS/HSTS values in `.env` to `true`, `31536000`, `true`, and `true`, and restart Gunicorn. Do not enable HSTS before HTTPS is verified.

## Verification

```bash
curl -I https://app.vetdojo.com/
curl https://app.vetdojo.com/health/
sudo systemctl status vetdojo-gunicorn vetdojo-celery redis-server nginx
sudo journalctl -u vetdojo-gunicorn -n 100 --no-pager
sudo journalctl -u vetdojo-celery -n 100 --no-pager
```

Only ports 80 and 443 should be public. Restrict SSH port 22 to approved IP addresses; do not expose ports 8000, 5432, or 6379.
