"""Keep private documents and local previews out of search engines too."""
from .seo import noindex_directive, response_is_indexable


class SearchIndexingMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        if not response_is_indexable(request, response):
            response['X-Robots-Tag'] = noindex_directive(request, response)
        return response
