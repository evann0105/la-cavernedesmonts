from django.apps import AppConfig


class ConnexionConfig(AppConfig):
	default_auto_field = 'django.db.models.BigAutoField'
	name = 'connexion'
	verbose_name = 'Connexion'

	def ready(self):
		from .reset_logging import install_password_reset_log_filter
		install_password_reset_log_filter()
