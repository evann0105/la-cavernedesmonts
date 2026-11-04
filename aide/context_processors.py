from connexion.email_verification import email_access_allowed
from core.context_processors import PUBLIC_PREVIEW_VIEWS
from tableau.access import can_access_seller_home
from .registry import page_articles


def seller_help(request):
    match = getattr(request, 'resolver_match', None)
    if (not request.path.startswith('/gestion/') or not match
            or match.view_name in PUBLIC_PREVIEW_VIEWS or match.namespace == 'aide'
            or not can_access_seller_home(request.user) or not email_access_allowed(request.user)):
        return {}
    articles = page_articles(request)
    return {'seller_page_help': articles}
