from django.http import Http404
from django.shortcuts import render
from django.views.decorators.http import require_GET
from tableau.access import seller_required
from tableau.home import _private
from .registry import CATEGORIES, available_articles, search_articles


@seller_required
@require_GET
def index(request):
    query = request.GET.get('q', '').strip()[:160]
    category = request.GET.get('rubrique', '')
    all_articles = available_articles(request.user)
    categories = [(key, label) for key, label in CATEGORIES if any(article['category'] == key for article in all_articles)]
    if category not in {key for key, label in categories}:
        category = ''
    articles = search_articles(request.user, query, category)
    groups = [{'key': key, 'label': label, 'articles': [article for article in articles if article['category'] == key]}
              for key, label in categories if any(article['category'] == key for article in articles)]
    if query:
        positions = {article['id']: position for position, article in enumerate(articles)}
        groups.sort(key=lambda group: positions[group['articles'][0]['id']])
    return _private(render(request, 'aide/index.html', {
        'query': query, 'selected_category': category, 'help_categories': categories,
        'help_groups': groups, 'result_count': len(articles), 'total_articles': len(all_articles),
    }))


@seller_required
@require_GET
def detail(request, slug):
    article = next((item for item in available_articles(request.user) if item['id'] == slug), None)
    if article is None:
        raise Http404
    related = [item for item in available_articles(request.user)
               if item['category'] == article['category'] and item['id'] != slug][:4]
    return _private(render(request, 'aide/detail.html', {'help_article': article, 'related_articles': related}))
