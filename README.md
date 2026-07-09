# 📝 Pisa

Pisa is a [Django](https://www.djangoproject.com/) website for teachers to design and assign programming and proof assignments in [Lean4](https://lean-lang.org/), Microsoft's open-source proof assistant.

## Install

This project uses Docker.

### Starting the project

Build and run with Docker:

```bash
docker compose up --build
```

Open the app at `http://127.0.0.1:8000/`.

If you need to run migrations later:

```bash
docker compose exec web python manage.py migrate
```

To stop the environment, run:

```bash
docker compose down
```

## Self-hosting

Pisa can run on your own server and domain with automatic HTTPS. The production stack ([`docker-compose.prod.yml`](docker-compose.prod.yml)) bundles [Caddy](https://caddyserver.com/), which fetches and renews a Let's Encrypt certificate for your domain and proxies HTTP and the Lean WebSocket to the app (served by `daphne`).

You need a server with Docker, ports **80** and **443** open, and a domain whose DNS **A/AAAA record points at the server**.

1. **Configure.** Copy the example env file and fill it in:

   ```bash
   cp .env.example .env
   # SECRET_KEY:  python -c "import secrets; print(secrets.token_urlsafe(50))"
   # PISA_DOMAIN: your hostname, e.g. lean.school.edu
   # DEBUG:       False
   ```

   Optionally set `DJANGO_SUPERUSER_USERNAME` / `DJANGO_SUPERUSER_PASSWORD` to create an admin on first boot.

2. **Launch.**

   ```bash
   docker compose -f docker-compose.prod.yml up -d --build
   ```

   The image bakes in the Lean toolchain, runs migrations, and collects static files on start; Caddy provisions TLS for `PISA_DOMAIN` automatically (this can take a few seconds on first run). Open `https://your-domain/`.

3. **Create the first admin** (if you didn't use the env vars above):

   ```bash
   docker compose -f docker-compose.prod.yml exec web python manage.py createsuperuser
   ```

### Data & backups

SQLite and uploaded media live on the host in `./data` and `./media`; Caddy's certificates live in a Docker volume. Back up `./data`, `./media`, and the `caddy_data` volume.

### Notes

- The stack runs a single app process with an in-memory channel layer and SQLite, which provides more than enough storage for a class or department. To scale across multiple processes you'd move to Postgres, a Redis channel layer, and a shared store for the per-user Lean-instance cap. See [TODO.md](TODO.md) for the deferred roadmap (scale-out, Lean performance, and more).
- Updating: `git pull` then re-run the `up -d --build` command above.

## Contributing

### Install Lean locally for development

If you want to run Lean on the host _outside_ Docker, install elan:

```bash
curl -sSf https://raw.githubusercontent.com/leanprover/elan/master/elan-init.sh | sh
source ~/.elan/env
lean --version
```
