"""Help content is static; resolving it never reads or changes customer records."""
import re
import unicodedata
from django.conf import settings
from django.urls import reverse
from core.catalog_access import can_manage_catalog
from retours.access import manager
from .content import general, catalog, site, service, recovery, pauses, photos

MODULES = (general, catalog, site, service, recovery, pauses, photos)
CATEGORIES = (
    ('quotidien', 'Prendre en main la boutique'),
    ('catalogue', 'Produits, rubriques et soldes'),
    ('stocks', 'Stocks et arrivages'),
    ('commandes', 'Commandes, expéditions et retours'),
    ('clients', 'Clients et SAV'),
    ('avoirs', 'Avoirs et codes clients'),
    ('accueil', 'Personnaliser l’accueil'),
    ('photos', 'Photos et bibliothèque'),
    ('pages', 'Pages, FAQ et annonces'),
    ('boutique', 'Coordonnées et horaires'),
    ('livraison', 'Livraison et transporteur'),
    ('alertes', 'E-mails et alertes'),
    ('comptabilite', 'Comptabilité et exports'),
)
ARTICLES = {article['id']: article for module in MODULES for article in module.ARTICLES}
PAGE_HELP = {name: ids for module in MODULES for name, ids in module.PAGE_HELP.items()}
PAGE_HELP_VARIANTS = tuple(item for module in MODULES for item in getattr(module, 'PAGE_HELP_VARIANTS', ()))
STOP_WORDS = frozenset('a au aux avec ce ces comment dans de des du en et faire il je la le les ma mes mon ou par pour que qui sa se ses son sur un une'.split())


def normalize(text):
    text = unicodedata.normalize('NFKD', text.casefold())
    text = ''.join(char for char in text if not unicodedata.combining(char))
    return ' '.join(re.findall(r'[a-z0-9]+', text))


def _terms(text):
    return [word for word in normalize(text).split() if word not in STOP_WORDS]


def permissions(user):
    catalog_access, service_access = bool(can_manage_catalog(user)), bool(manager(user))
    return {'catalog': catalog_access, 'service': service_access, 'seller': catalog_access or service_access}


def _allowed(item, access):
    return access.get(item.get('permission', 'seller'), False) and (
        not item.get('test_only') or (settings.DEBUG and settings.EMAIL_TEST_MODE))


def prepare(article, access):
    actions = []
    for action in article.get('actions', ()):
        if _allowed(action, access):
            url = reverse(action['view'], kwargs=action.get('kwargs'))
            if action.get('fragment'):
                url += '#' + action['fragment']
            actions.append({**action, 'url': url})
    return {**article, 'actions': actions, 'url': reverse('aide:detail', args=[article['id']]),
            'category_label': dict(CATEGORIES)[article['category']]}


def available_articles(user):
    access = permissions(user)
    return [prepare(article, access) for article in ARTICLES.values() if _allowed(article, access)]


def page_articles(request):
    match = request.resolver_match
    if not match:
        return []
    ids = PAGE_HELP.get(match.view_name, ('retrouver-une-fonction', 'utiliser-aide'))
    for variant in PAGE_HELP_VARIANTS:
        if variant['view'] == match.view_name and all(match.kwargs.get(key) == value for key, value in variant['kwargs'].items()):
            ids = variant['articles']
            break
    access = permissions(request.user)
    return [prepare(ARTICLES[key], access) for key in ids if _allowed(ARTICLES[key], access)]


def search_articles(user, query='', category=''):
    terms = _terms(query)
    articles = []
    for article in available_articles(user):
        if category and article['category'] != category:
            continue
        text = normalize(' '.join((article['title'], article['summary'], *article['steps'],
                                   *article.get('notes', ()), *article.get('keywords', ()))))
        if all(term in text for term in terms):
            articles.append(article)
    if terms:
        # A task explicitly named by a title comes before an incidental mention.
        def relevance(article):
            title, summary = normalize(article['title']), normalize(article['summary'])
            return (all(term in title for term in terms),
                    sum(term in title for term in terms), sum(term in summary for term in terms))
        articles.sort(key=relevance, reverse=True)
    return articles
