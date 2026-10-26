from django import forms


class AlertSettingsForm(forms.Form):
    revision = forms.IntegerField(min_value=0, widget=forms.HiddenInput)
    recipient = forms.EmailField(label='Adresse e-mail de la vendeuse', required=False, max_length=254,
        help_text='L’adresse qui reçoit les nouvelles commandes et les alertes de stock. Elle ne remplace pas l’e-mail public de la boutique.')
    order_notifications_enabled = forms.BooleanField(label='Me prévenir à chaque nouvelle commande payée', required=False,
        help_text='Après confirmation du paiement, avec un accès direct à la préparation de la commande.')
    stock_notifications_enabled = forms.BooleanField(label='Recevoir les alertes de stock par e-mail', required=False,
        help_text='Une alerte lorsqu’une taille et couleur atteint son seuil. Une nouvelle alerte est possible après réapprovisionnement.')
    stock_threshold = forms.IntegerField(label='Me prévenir à partir de ce nombre d’unités restantes', min_value=0, max_value=99999,
        help_text='Exemple : 3 déclenche une alerte à 3 unités ou moins. Les unités réservées pour une commande sont déduites. 0 signifie uniquement les ruptures.')

    def clean_recipient(self):
        return self.cleaned_data.get('recipient', '').strip().lower()

    def clean(self):
        values = super().clean()
        if (values.get('order_notifications_enabled') or values.get('stock_notifications_enabled')) and not values.get('recipient'):
            self.add_error('recipient', 'Indiquez l’adresse qui doit recevoir les alertes.')
        return values


class StockRuleForm(forms.Form):
    revision = forms.IntegerField(min_value=1, widget=forms.HiddenInput)
    enabled = forms.BooleanField(label='Surveiller cette taille et cette couleur', required=False)
    threshold = forms.IntegerField(label='Seuil spécifique (facultatif)', required=False, min_value=0, max_value=99999,
        help_text='Laissez vide pour suivre le seuil par défaut de la boutique. 0 signifie uniquement la rupture.')
