"""The merchant's ordered menu, shared by desktop and mobile navigation."""
from .models import Category


def category_navigation(request):
    categories = list(Category.objects.filter(show_in_menu=True, archived_at__isnull=True))
    return {'navigation_categories': categories,
            'navigation_main_categories': categories[:4],
            'navigation_more_categories': categories[4:]}
