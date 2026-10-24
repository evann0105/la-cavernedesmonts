from django import forms


class CategoryForm(forms.Form):
    name = forms.CharField(label='Nom de la rubrique', max_length=120,
        help_text='Par exemple : Accessoires de randonnée ou Nouveautés.')
    menu_label = forms.CharField(label='Nom court dans le menu', max_length=40, required=False,
        help_text='Facultatif. Sans nom court, le menu reprend le nom de la rubrique.')
    show_in_menu = forms.BooleanField(label='Afficher cette rubrique dans le menu du site', required=False,
        help_text='Elle apparaîtra sur ordinateur et sur téléphone. Les autres rubriques restent accessibles dans le catalogue.')
    revision = forms.IntegerField(min_value=0, widget=forms.HiddenInput)

    def clean_name(self):
        return ' '.join(self.cleaned_data['name'].split())

    def clean_menu_label(self):
        return ' '.join(self.cleaned_data['menu_label'].split())


class CategoryMoveForm(forms.Form):
    move = forms.RegexField(regex=r'^[1-9][0-9]*:(up|down)$', max_length=40, widget=forms.HiddenInput,
        error_messages={'invalid': 'Choisissez une rubrique à monter ou à descendre.'})
    snapshot = forms.CharField(max_length=100000, widget=forms.HiddenInput)


class CategoryVisibilityForm(forms.Form):
    revision = forms.IntegerField(min_value=1, widget=forms.HiddenInput)
    show_in_menu = forms.ChoiceField(choices=[('0', 'Masquer'), ('1', 'Afficher')], widget=forms.HiddenInput)
