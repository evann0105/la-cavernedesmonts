"""Development-only response headers; never installed in production settings."""
from django.utils.cache import add_never_cache_headers


class LocalHttpMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        # Prevent development redirects (including canonical URL redirects) from
        # lingering in the browser after a configuration or branch change.
        add_never_cache_headers(response)
        response['X-Caverne-Local'] = 'http'
        return response
