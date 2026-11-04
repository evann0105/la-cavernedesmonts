"""Paris-local calendars and explicit confirmations for checkout pauses."""
from zoneinfo import ZoneInfo

from django import forms
from django.utils import timezone

PARIS = ZoneInfo('Europe/Paris')


class ParisDateTimeField(forms.DateTimeField):
    default_error_messages = {
        'invalid': 'Indiquez une date et une heure valides.',
        'ambiguous_timezone': 'Cette heure n’existe pas ou se produit deux fois lors du changement d’heure à Paris. Choisissez une autre heure.',
    }

    def to_python(self, value):
        with timezone.override(PARIS):
            try:
                return super().to_python(value)
            except forms.ValidationError as error:
                if error.code == 'ambiguous_timezone':
                    raise forms.ValidationError(self.error_messages['ambiguous_timezone'], code='ambiguous_timezone') from error
                raise

    def prepare_value(self, value):
        with timezone.override(PARIS):
            return super().prepare_value(value)


def calendar_field(label, *, required=True):
    return ParisDateTimeField(label=label, required=required,
        input_formats=['%Y-%m-%dT%H:%M', '%Y-%m-%dT%H:%M:%S'],
        widget=forms.DateTimeInput(format='%Y-%m-%dT%H:%M', attrs={'type': 'datetime-local'}))


DEFAULT_MESSAGE = 'Les achats en ligne font une pause. Retrouvez toute la collection et préparez vos envies pour notre retour.'


class PauseForm(forms.Form):
    revision = forms.IntegerField(min_value=0, max_value=2147483647, widget=forms.HiddenInput)
    name = forms.CharField(label='Nom de cette pause', max_length=120,
        help_text='Visible uniquement par vous. Exemple : Vacances de février.')
    mode = forms.ChoiceField(label='Quand commencer ?',
        choices=(('scheduled', 'Choisir une date et une heure'), ('now', 'Dès maintenant')),
        widget=forms.RadioSelect)
    starts_at = calendar_field('Début de la pause', required=False)
    ends_at = calendar_field('Reprise automatique des commandes')
    message = forms.CharField(label='Message affiché aux clients', max_length=300,
        widget=forms.Textarea(attrs={'rows': 4}),
        help_text='300 caractères maximum. La date de reprise est ajoutée automatiquement.')
    confirmed = forms.BooleanField(label='Je confirme cette pause des nouvelles commandes et la date de reprise.',
        error_messages={'required': 'Confirmez la pause après avoir vérifié ses dates et son message.'})

    def __init__(self, *args, pause=None, source=None, revision=0, now=None, immediate=False, **kwargs):
        if pause is not None and source is not None:
            raise ValueError('Une duplication doit créer une nouvelle pause.')
        self.pause = pause
        self.now = now or timezone.now()
        self.already_started = bool(pause and pause.starts_at <= self.now)
        initial = {'revision': revision, 'mode': 'now' if immediate and source is None else 'scheduled',
                   'name': 'Pause des commandes', 'message': DEFAULT_MESSAGE}
        if source is not None:
            # A source is a text template, never an editable instance. Its
            # dates, stop state, confirmation and revision are not inherited.
            initial.update(name=source.name, message=source.message)
        if pause:
            initial.update(name=pause.name, message=pause.message,
                           starts_at=pause.starts_at, ends_at=pause.ends_at, mode='scheduled')
        initial.update(kwargs.pop('initial', {}) or {})
        super().__init__(*args, initial=initial, **kwargs)
        self.fields['starts_at'].help_text = 'Heure de Paris. Ce champ est ignoré si vous choisissez « Dès maintenant ».'
        self.fields['ends_at'].help_text = 'Heure de Paris. Les achats reprennent automatiquement à cet instant.'
        if self.is_bound and self.data.get('mode') == 'now' and not self.already_started:
            self.fields['starts_at'].disabled = True
        if self.already_started:
            self.fields['mode'].widget = forms.HiddenInput()
            self.fields['mode'].disabled = True
            self.fields['starts_at'].widget = forms.HiddenInput()
            self.fields['starts_at'].disabled = True
        elif pause:
            self.fields['mode'].choices = (('scheduled', 'Choisir une date et une heure'), ('now', 'Commencer maintenant'))

    def clean(self):
        data = super().clean()
        start = data.get('starts_at')
        end = data.get('ends_at')
        if data.get('mode') == 'now':
            start = self.now
            data['starts_at'] = start
        elif not start:
            self.add_error('starts_at', 'Choisissez le début de la pause, ou « Dès maintenant ».')
        elif not self.already_started and start <= self.now:
            self.add_error('starts_at', 'Choisissez une date future, ou « Dès maintenant ».')
        if end and end <= self.now:
            self.add_error('ends_at', 'La reprise doit être dans le futur.')
        elif start and end and end <= start:
            self.add_error('ends_at', 'La reprise doit être après le début de la pause.')
        return data


class PauseActionForm(forms.Form):
    revision = forms.IntegerField(min_value=0, max_value=2147483647, widget=forms.HiddenInput)
    confirmed = forms.BooleanField(label='Je confirme cette action.',
        error_messages={'required': 'Cochez la confirmation pour continuer.'})
