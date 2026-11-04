from django.apps import AppConfig

class PaiementConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'paiement'

    def ready(self):
        from .tls import configure_stripe_tls
        configure_stripe_tls()
