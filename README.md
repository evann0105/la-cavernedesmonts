# La Caverne des Monts - Django

Démarrage rapide pour un site Django.

## Prérequis
- Python 3.10+
- macOS zsh

## Installation
```sh
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
```

## Configuration
- Variables d'env dans `.env` (déjà créé): DEBUG, SECRET_KEY, ALLOWED_HOSTS

## Lancer le serveur
```sh
. .venv/bin/activate
python manage.py migrate
python manage.py runserver
```

Ouvrez http://127.0.0.1:8000/

## Structure
- `config/` projet Django
- `core/` app principale (page d'accueil)
- `templates/` templates HTML
- `static/` assets

## Déploiement
- Définir `DEBUG=False`, `ALLOWED_HOSTS`, `SECRET_KEY`
- Collecte des statiques: `python manage.py collectstatic`
