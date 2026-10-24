"""Request-local public editorial content; scheduled announcements are never cached."""
from django.utils.translation import gettext
from .editorial_configuration import active_announcement, page_content


def page(request, key):
    content = page_content(key)
    # Existing French defaults retain their translations. Custom copy stays as
    # authored when it has no translation in the site's message catalogue.
    content['values'] = {name: '\n\n'.join(gettext(part) for part in value.split('\n\n'))
                         for name, value in content['values'].items()}
    request._editorial_page = {'key': key, **content}
    return content


def announcement(request):
    if not hasattr(request, '_site_announcement'):
        request._site_announcement = active_announcement()
    return {'site_announcement': request._site_announcement}
