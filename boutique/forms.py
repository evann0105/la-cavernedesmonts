from django import forms
from django.core.exceptions import ValidationError
from django.forms import BaseInlineFormSet, inlineformset_factory
from .models import OpeningDay, OpeningHours


class OpeningHoursForm(forms.ModelForm):
    revision = forms.IntegerField(min_value=1, widget=forms.HiddenInput)

    class Meta:
        model = OpeningHours
        fields = ['notice']
        widgets = {'notice': forms.Textarea(attrs={'rows': 3, 'maxlength': 600})}
        help_texts = {'notice': 'Facultatif : précisez ici vos horaires de saison ou une information pour les visiteurs.'}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['revision'].initial = self.instance.revision


class OpeningDayForm(forms.ModelForm):
    class Meta:
        model = OpeningDay
        fields = ['is_closed', 'opens_at', 'closes_at', 'reopens_at', 'recloses_at']
        widgets = {field: forms.TimeInput(format='%H:%M', attrs={'type': 'time', 'step': 60})
                   for field in ('opens_at', 'closes_at', 'reopens_at', 'recloses_at')}

    @property
    def day_label(self):
        return self.instance.get_weekday_display()

    def clean(self):
        data = super().clean()
        if data.get('is_closed'):
            for field in ('opens_at', 'closes_at', 'reopens_at', 'recloses_at'):
                data[field] = None
                self._errors.pop(field, None)
        return data


class WeeklyHoursFormSet(BaseInlineFormSet):
    def clean(self):
        super().clean()
        expected = list(self.get_queryset().values_list('pk', flat=True))
        submitted = [form.cleaned_data.get('id') for form in self.forms]
        if (len(expected) != 7 or len(submitted) != 7
                or [getattr(day, 'pk', None) for day in submitted] != expected
                or [form.instance.weekday for form in self.forms] != list(range(7))
                or self.initial_form_count() != 7):
            raise ValidationError('Le planning doit contenir les sept jours dans l’ordre. Rechargez la page et réessayez.')


OpeningDaysFormSet = inlineformset_factory(
    OpeningHours, OpeningDay, form=OpeningDayForm, formset=WeeklyHoursFormSet,
    extra=0, can_delete=False, min_num=7, max_num=7, validate_min=True, validate_max=True,
    absolute_max=7,
)
