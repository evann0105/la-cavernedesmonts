from django.core.exceptions import ImproperlyConfigured


def validate_secret_key(value):
    lowered = value.lower()
    if (len(value) < 50 or len(set(value)) < 8 or
            any(word in lowered for word in ('insecure', 'change-me', 'remplacer', 'example'))):
        raise ImproperlyConfigured('SECRET_KEY doit contenir une clé aléatoire forte de 50 caractères minimum. Utilisez ./start en local ou configurez un secret de production.')
    return value
