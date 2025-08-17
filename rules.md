# Règles d'Organisation du Projet Django

## Structure des Applications
- **Une application = Une fonctionnalité** (ex: `connexion`, `budget`, `profil`)
- Créer une app par page/section principale

## Organisation des Fichiers

⚠️ **Important** : Chaque application doit avoir ses propres dossiers `templates/` et `static/`
⚠️ **Important** : les fichiers html ne doivent pas contenir de css ou de js, ils doivent etre rangé dans le bon endroit

### Structure par Application
```
connexion/
├── static/connexion/
│   ├── css/
│   │   └── login.css
│   └── js/
│       └── login.js
└── templates/connexion/
    ├── login.html
    └── register.html

budget/
├── static/budget/
│   ├── css/
│   │   └── dashboard.css
│   └── js/
│       └── dashboard.js
└── templates/budget/
    ├── dashboard.html
    └── add_expense.html
```

### Templates HTML Globaux
```
templates/           # Dans le projet principal seulement
└── base.html       # Template de base commun
```

## Structure d'une Application Django
```
mon_app/
├── __init__.py
├── admin.py          # Configuration admin
├── apps.py           # Configuration de l'app
├── models.py         # Modèles de données
├── views.py          # Logique métier
├── urls.py           # URLs de l'app
├── forms.py          # Formulaires
├── tests.py          # Tests
└── migrations/       # Migrations DB
```

## Bonnes Pratiques
- **Noms en français** pour les apps (connexion, budget, profil)
- **Un seul concept par app** (éviter les apps fourre-tout)
- **Fichiers statiques dans chaque app** (`static/nom_app/`)
- **Templates dans chaque app** (`templates/nom_app/`)
- **Une app = ses propres ressources** (CSS, JS, templates isolés)
