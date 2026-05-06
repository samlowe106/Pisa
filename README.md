# Pisa

Pisa is a website for teachers to design and assign programming and proof assignments in [Lean4](https://lean-lang.org/), Microsoft's open-source proof assistant. Pisa is powered by [Django](https://www.djangoproject.com/).

## Install

This project uses Docker.

### Install Lean locally for development

If you want to run Lean on the host outside Docker, install elan:

```bash
curl -sSf https://raw.githubusercontent.com/leanprover/elan/master/elan-init.sh | sh
source ~/.elan/env
lean --version
```

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
