"""Every management route is accounted for, including non-page endpoints.

The coverage test fails when a new management URL has no contextual help and
has not been explicitly identified as a download, mutation or preview frame.
"""
from .registry import MODULES

NON_PAGE_ROUTES = {
    **{name: reason for module in MODULES for name, reason in getattr(module, 'NON_PAGE_ROUTES', {}).items()},
    'test_email_preview': 'HTML du message affiché dans un aperçu isolé, sans interface de gestion.',
    'core:catalog_preview': 'Aperçu de la fiche publique : conserve le rendu vu par le client.',
    'editorial:page_preview_frame': 'Contenu public de l’aperçu, inclus dans la page de gestion déjà guidée.',
    'editorial:page_version_frame': 'Contenu public d’une ancienne version, inclus dans son écran de gestion.',
    'suspensions:dashboard_status': 'État privé des pauses, actualisé dans l’encadré déjà guidé de l’accueil vendeuse.',
}
