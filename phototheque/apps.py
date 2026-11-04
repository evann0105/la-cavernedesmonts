from django.apps import AppConfig


class PhotothequeConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'phototheque'
    verbose_name = 'Bibliothèque de photos'

    def ready(self):
        from .signals import connect
        connect()
