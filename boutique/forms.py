from django import forms
from django.core.exceptions import ValidationError
from django.forms import BaseInlineFormSet, inlineformset_factory
from .models import OpeningDay, OpeningHours, OpeningPeriod, OpeningPeriodDay


class OpeningHoursForm(forms.ModelForm):
    revision = forms.IntegerField(min_value=1, widget=forms.HiddenInput)

    class Meta:
        model = OpeningHours
        fields = ['notice']
        widgets = {'notice': forms.Textarea(attrs={'rows': 3, 'maxlength': 600})}
        help_texts = {'notice': 'Facultatif : cette information accompagne les horaires habituels, en dehors des périodes exceptionnelles.'}

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


class OpeningPeriodForm(forms.ModelForm):
    revision = forms.IntegerField(min_value=0, widget=forms.HiddenInput)

    class Meta:
        model = OpeningPeriod
        fields = ['name', 'start_date', 'end_date', 'notice']
        widgets = {
            'name': forms.TextInput(attrs={'placeholder': 'Exemple : vacances de Noël'}),
            'start_date': forms.DateInput(format='%Y-%m-%d', attrs={'type': 'date'}),
            'end_date': forms.DateInput(format='%Y-%m-%d', attrs={'type': 'date'}),
            'notice': forms.Textarea(attrs={'rows': 3, 'maxlength': 600}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['revision'].initial = self.instance.revision if self.instance.pk else 0


class OpeningPeriodDayForm(OpeningDayForm):
    class Meta(OpeningDayForm.Meta):
        model = OpeningPeriodDay

    def has_changed(self):
        # A new period needs all seven rows, even when the copied hours are unchanged.
        return not self.instance.pk or super().has_changed()


class PeriodDaysFormSet(WeeklyHoursFormSet):
    def _construct_form(self, index, **kwargs):
        form = super()._construct_form(index, **kwargs)
        if not self.instance.pk:
            form.instance.weekday = index
        return form

    def clean(self):
        if self.instance.pk:
            return super().clean()
        BaseInlineFormSet.clean(self)
        if (len(self.forms) != 7 or self.initial_form_count() != 0
                or any(form.cleaned_data.get('id') for form in self.forms)
                or [form.instance.weekday for form in self.forms] != list(range(7))):
            raise ValidationError('Le planning doit contenir les sept jours dans l’ordre. Rechargez la page et réessayez.')


OpeningPeriodDaysFormSet = inlineformset_factory(
    OpeningPeriod, OpeningPeriodDay, form=OpeningPeriodDayForm, formset=PeriodDaysFormSet,
    extra=0, can_delete=False, min_num=7, max_num=7, validate_min=True, validate_max=True,
    absolute_max=7,
)


class PeriodActionForm(forms.Form):
    revision = forms.IntegerField(min_value=1, widget=forms.HiddenInput)
