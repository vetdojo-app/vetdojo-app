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

Copy `.env.production.example` to `/srv/vetdojo/web_app/.env`, replace every
placeholder, and give it permissions `600`. The completed file must remain only
on the server and in an approved secret store. Its production values include:

```env
DJANGO_SECRET_KEY=generate-a-long-random-value
DJANGO_DEBUG=false
DJANGO_ALLOWED_HOSTS=app.vetdojo.com
CSRF_TRUSTED_ORIGINS=https://app.vetdojo.com
USE_SQLITE=false
DATABASE_URL=postgresql://...
OPENAI_API_KEY=...
OPENAI_PDF_MODEL=gpt-4.1-mini
OPENAI_PDF_ANALYSIS_ENABLED=true
PRIVATE_MEDIA_ROOT=/srv/vetdojo/private_media
JOURNAL_PDF_MAX_BYTES=26214400
JOURNAL_PDF_MAX_PAGES=500
JOURNAL_PDF_MIN_TEXT_CHARS=200
CELERY_BROKER_URL=redis://127.0.0.1:6379/0
CELERY_RESULT_BACKEND=redis://127.0.0.1:6379/0
DJANGO_SECURE_SSL_REDIRECT=false
DJANGO_SECURE_HSTS_SECONDS=0
DJANGO_SECURE_HSTS_INCLUDE_SUBDOMAINS=false
DJANGO_SECURE_HSTS_PRELOAD=false
```

Before enabling HSTS, verify HTTPS and every required subdomain. Never commit `.env`.

Create the private journal storage directory. It must not be added to an Nginx
`location` block or otherwise exposed as public media:

```bash
sudo install -d -m 0750 -o ubuntu -g www-data /srv/vetdojo/private_media
```

## Database and static assets

Back up the production database before the first migration, then:

```bash
cd /srv/vetdojo/web_app
.venv/bin/python manage.py check
.venv/bin/python manage.py migrate
.venv/bin/python manage.py collectstatic --noinput
.venv/bin/python manage.py createsuperuser
```

Migrations `0011` through `0013` add private journal-article records, extracted
pages, and citation metadata. Back up the database before applying them.

## Backups

Back up both PostgreSQL and `/srv/vetdojo/private_media`. A database-only backup
does not include uploaded journal PDFs. Test restoration of the database and the
private files together. Deleting or replacing a journal-article record removes
its old private file after the database transaction commits.

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

After HTTPS is verified, use the recommended final values: SSL redirect `true`,
HSTS seconds `31536000`, include-subdomains `false`, and preload `false`. This
avoids making assumptions about other VetDojo subdomains. Restart Gunicorn
after changing the server `.env`.

## Updating an existing installation

Merge the reviewed feature branch into `main`, then on the server:

```bash
cd /srv/vetdojo/web_app
git status --short
git pull --ff-only origin main
.venv/bin/pip install -r requirements.txt
sudo install -d -m 0750 -o ubuntu -g www-data /srv/vetdojo/private_media
.venv/bin/python manage.py check
.venv/bin/python manage.py migrate
.venv/bin/python manage.py collectstatic --noinput
sudo cp deploy/systemd/vetdojo-gunicorn.service /etc/systemd/system/
sudo cp deploy/systemd/vetdojo-celery.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl restart redis-server vetdojo-celery vetdojo-gunicorn
```

Certbot modifies the live Nginx file when it enables HTTPS. Do not overwrite
that file during an application update. Instead, edit
`/etc/nginx/sites-available/app.vetdojo.com`, change
`client_max_body_size` to `30m` in the existing server block, and validate it:

```bash
sudo nginx -t
sudo systemctl reload nginx
```

Do not run `git pull` if `git status --short` reports unexpected server-side
changes. Preserve the server `.env` and private PDF directory; neither belongs
in Git.

## Verification

```bash
curl -I https://app.vetdojo.com/
curl https://app.vetdojo.com/health/
sudo systemctl status vetdojo-gunicorn vetdojo-celery redis-server nginx
sudo journalctl -u vetdojo-gunicorn -n 100 --no-pager
sudo journalctl -u vetdojo-celery -n 100 --no-pager
```

Only ports 80 and 443 should be public. Restrict SSH port 22 to approved IP addresses; do not expose ports 8000, 5432, or 6379.
