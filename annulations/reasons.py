"""Public cancellation reason suggestions, shared by forms and service validation."""

REASON_PRESETS = (
    ('client_request', 'Demande du client', 'Annulation à la demande du client.'),
    ('order_error', 'Erreur de commande', 'Annulation à la suite d’une erreur de commande.'),
    ('unavailable', 'Article indisponible', 'L’article commandé n’est plus disponible.'),
    ('other', 'Autre', 'Annulation de la commande avant expédition.'),
)
REASON_CHOICES = tuple((code, label) for code, label, text in REASON_PRESETS)
REASON_TEXTS = {code: text for code, label, text in REASON_PRESETS}
PUBLIC_REASON_MAX_LENGTH = 1000
INTERNAL_NOTE_MAX_LENGTH = 2000


def public_reason(reason, code=''):
    """Keep custom wording; a known preset supplies an otherwise empty reason."""
    if not isinstance(code, str) or (code and code not in REASON_TEXTS):
        raise ValueError('Choisissez un motif proposé ou laissez ce choix vide.')
    if not isinstance(reason, str):
        raise ValueError('Indiquez le motif communiqué au client (1 000 caractères maximum).')
    value = reason.strip() or REASON_TEXTS.get(code, '')
    if not 1 <= len(value) <= PUBLIC_REASON_MAX_LENGTH:
        raise ValueError('Indiquez le motif communiqué au client (1 000 caractères maximum).')
    return value


def internal_note_text(note):
    if not isinstance(note, str) or len(note.strip()) > INTERNAL_NOTE_MAX_LENGTH:
        raise ValueError('La note interne doit contenir au maximum 2 000 caractères.')
    return note.strip()
