"""Conservative name-based classification, overridable by the merchant."""
from django.db.models import Q, Case, When, Value, CharField


def with_article_type(products):
    rules = [('polaires', ['polaire']), ('vestes', ['veste', 'blouson', 'softshell', 'doudoune', 'parka']), ('pantalons', ['pantalon', 'short', 'bermuda']), ('hauts', ['pull', 'sweat', 'tee shirt', 't-shirt', 'polo', 'chemise']), ('accessoires', ['bonnet', 'gant', 'écharpe', 'chaussette', 'bandeau', 'cagoule', 'tour de cou'])]
    conditions = []
    for value, words in rules:
        query = Q()
        for word in words:
            query |= Q(name__icontains=word)
        conditions.append(When(query, then=Value(value)))
    return products.annotate(resolved_type=Case(When(~Q(article_type=''), then='article_type'), *conditions, default=Value(''), output_field=CharField()))
