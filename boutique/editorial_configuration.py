"""Read-only public content. Reading a page never creates a database row."""
from django.templatetags.static import static
from django.utils import timezone

from .editorial_schema import PAGE_SPECS, target_url


def _page_instance(key, instance):
    if key not in PAGE_SPECS:
        raise ValueError('Cette page n’existe pas.')
    if instance is None:
        from .models import ContentPage
        instance = ContentPage.objects.filter(key=key).first()
    return instance


def _content(key, data, photo, revision, updated_at=None, published_revision=0):
    values = {name: spec['default'] for name, spec in PAGE_SPECS[key]['fields'].items()}
    if isinstance(data, dict):
        for name, spec in PAGE_SPECS[key]['fields'].items():
            value = data.get(name)
            if isinstance(value, str) and len(value) <= spec['max_length'] and (value.strip() or not spec['required']):
                values[name] = value
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
    }, 'revision': revision, 'published_revision': published_revision, 'updated_at': updated_at}


def page_content(key, instance=None):
    """Public content only. A draft must never affect this reader or SEO metadata."""
    instance = _page_instance(key, instance)
    return _content(key, instance.data if instance else {}, getattr(instance, 'image', None),
        instance.revision if instance else 0, instance.updated_at if instance else None,
        instance.published_revision if instance else 0)


def page_draft_content(key, instance=None):
    instance = _page_instance(key, instance)
    if instance is not None and instance.has_draft:
        return _content(key, instance.draft_data, instance.draft_image, instance.revision,
            instance.updated_at, instance.published_revision)
    return page_content(key, instance=instance)


def page_version_content(version, revision=None):
    return _content(version.page.key, version.data, version.image,
        version.page.revision if revision is None else revision, version.created_at,
        version.publication_revision)


def announcement_overlaps(starts_at, ends_at, *, exclude_pk=None, at=None):
    from .models import SiteAnnouncement
    at = timezone.now() if at is None else at
    if not starts_at or not ends_at or ends_at <= starts_at or ends_at <= at:
        return SiteAnnouncement.objects.none()
    # Only a remaining overlap can affect the banner the seller is preparing.
    # Completed periods are history, not a current publication conflict.
    entries = SiteAnnouncement.objects.filter(enabled=True, starts_at__lt=ends_at,
                                              ends_at__gt=max(starts_at, at))
    if exclude_pk is not None:
        entries = entries.exclude(pk=exclude_pk)
    return entries.order_by('-priority', '-starts_at', '-pk')


def announcement_rows(entries, displayed, *, at):
    """All status labels share the same clock and the exact public winner."""
    entries = list(entries)
    labels = {'draft': 'Brouillon', 'scheduled': 'Prévue', 'ended': 'Terminée',
              'displayed': 'Affichée sur le site',
              'superseded': 'Non affichée · autre annonce prioritaire'}
    rows = []
    for entry in entries:
        state = entry.status_at(at)
        if state == 'active':
            state = 'displayed' if displayed and entry.pk == displayed.pk else 'superseded'
        overlaps = sum(1 for other in entries if entry.enabled and other.enabled and
            entry.ends_at > at and other.ends_at > at and other.pk != entry.pk and entry.starts_at < other.ends_at and other.starts_at < entry.ends_at)
        rows.append({'announcement': entry, 'display_state': state,
                     'display_label': labels[state], 'overlap_count': overlaps})
    return rows


def public_faq():
    from .models import FAQEntry
    return FAQEntry.objects.filter(enabled=True).order_by('position', 'pk')


def active_announcement(at=None):
    from .models import SiteAnnouncement
    at = timezone.now() if at is None else at
    return SiteAnnouncement.objects.filter(enabled=True, starts_at__lte=at, ends_at__gt=at).order_by(
        '-priority', '-starts_at', '-pk').first()
