"""Read-only public content. Reading a page never creates a database row."""
from django.templatetags.static import static
from django.utils import timezone

from .editorial_schema import PAGE_SPECS, target_url


def page_content(key, instance=None):
    if key not in PAGE_SPECS:
        raise ValueError('Cette page n’existe pas.')
    if instance is None:
        from .models import ContentPage
        instance = ContentPage.objects.filter(key=key).first()
    values = {name: spec['default'] for name, spec in PAGE_SPECS[key]['fields'].items()}
    if instance is not None and isinstance(instance.data, dict):
        for name, spec in PAGE_SPECS[key]['fields'].items():
            value = instance.data.get(name)
            if isinstance(value, str) and len(value) <= spec['max_length'] and (value.strip() or not spec['required']):
                values[name] = value
    photo = getattr(instance, 'image', None)
    custom = bool(photo and photo.name)
    width, height = 2400, 1594
    if custom:
        try:
            if type(photo.width) is int and type(photo.height) is int and photo.width > 0 and photo.height > 0:
                width, height = photo.width, photo.height
        except (OSError, ValueError, AttributeError):
            pass
    return {'values': values, 'image': {
        'url': photo.url if custom else static('core/img/boutique/lelex-2400.jpg'),
        'custom': custom, 'width': width, 'height': height,
    }, 'revision': instance.revision if instance is not None else 0,
        'updated_at': instance.updated_at if instance is not None else None}


def public_faq():
    from .models import FAQEntry
    return FAQEntry.objects.filter(enabled=True).order_by('position', 'pk')


def active_announcement(at=None):
    from .models import SiteAnnouncement
    at = timezone.now() if at is None else at
    return SiteAnnouncement.objects.filter(enabled=True, starts_at__lte=at, ends_at__gt=at).order_by(
        '-priority', '-starts_at', '-pk').first()
