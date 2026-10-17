import uuid
from django import forms
from .services import remaining_items, declaration_identity


class LookupForm(forms.Form):
    order_id = forms.UUIDField(label='Référence de la commande')
    email = forms.EmailField(label='Adresse e-mail utilisée pour la commande')


class RequestForm(forms.Form):
    submission_id = forms.UUIDField(widget=forms.HiddenInput)
    customer_name = forms.CharField(label='Votre nom et prénom', max_length=200, widget=forms.TextInput(attrs={'autocomplete':'name'}))
    confirmation_email = forms.EmailField(label='Adresse e-mail de réception de l’accusé', disabled=True, help_text='L’accusé de réception est envoyé à cette adresse, utilisée lors de la commande. En cas de difficulté d’accès à votre messagerie, vous pouvez aussi notifier votre rétractation directement à la boutique.')
    kind = forms.ChoiceField(label='Votre demande',choices=[('withdrawal','Me rétracter'),('problem','Signaler un article défectueux ou une erreur')])
    reason = forms.CharField(label='Précision facultative',required=False,max_length=1000,widget=forms.Textarea(attrs={'rows':3}))
    confirm = forms.BooleanField(label='Je confirme envoyer cette déclaration pour les articles sélectionnés et recevoir son accusé à l’adresse e-mail indiquée.')

    def __init__(self,order,*args,**kwargs):
        super().__init__(*args,**kwargs)
        self.fields['submission_id'].initial=uuid.uuid4()
        identity = declaration_identity(order)
        self.fields['customer_name'].initial = identity['name']
        self.fields['confirmation_email'].initial = identity['email']
        self.rows=remaining_items(order)
        for row in self.rows:
            self.fields[f"quantity_{row['index']}"]=forms.IntegerField(label=f"{row['name']} — {row.get('size','')} {row.get('color','')} (reste : {row['remaining']})",min_value=0,max_value=row['remaining'],initial=0,widget=forms.NumberInput(attrs={'inputmode':'numeric'}))
        keys=[f"quantity_{r['index']}" for r in self.rows]
        self.order_fields(['submission_id','customer_name','confirmation_email',*keys,'kind','reason','confirm'])

    def clean_customer_name(self):
        return ' '.join(self.cleaned_data['customer_name'].split())

    def quantities(self):
        return {r['index']:self.cleaned_data[f"quantity_{r['index']}"] for r in self.rows}


class CompleteForm(forms.Form):
    confirm = forms.BooleanField(label='J’ai vérifié les quantités reçues et revendables avant de consulter le récapitulatif.')

    def __init__(self,record,*args,**kwargs):
        super().__init__(*args,**kwargs)
        self.record = record
        self.rows=list(record.lines.select_related('variant').all())
        for row in self.rows:
            self.fields[f'received_{row.pk}']=forms.IntegerField(label=f'{row.name} — {row.size} {row.color} : quantité reçue',min_value=0,max_value=row.quantity,initial=row.quantity)
            self.fields[f'restock_{row.pk}']=forms.IntegerField(label='Dont quantité revendable à remettre en stock',min_value=0,max_value=row.quantity,initial=0,help_text='Laissez zéro pour les articles abîmés ou non revendables.')
        self.order_fields([key for row in self.rows for key in (f'received_{row.pk}',f'restock_{row.pk}')]+['confirm'])

    def clean(self):
        data = super().clean()
        for row in self.rows:
            received, restock = data.get(f'received_{row.pk}'), data.get(f'restock_{row.pk}')
            if received is not None and restock is not None and restock > received:
                self.add_error(f'restock_{row.pk}', 'La quantité revendable ne peut pas dépasser la quantité reçue.')
            if restock and not self.record.order.is_demo and (row.variant is None or row.variant.quantity is None):
                self.add_error(f'restock_{row.pk}', 'Renseignez d’abord le stock de cette variante dans l’inventaire.')
        return data

    def counts(self,prefix):
        return {r.pk:self.cleaned_data[f'{prefix}_{r.pk}'] for r in self.rows}


class ShipmentForm(forms.Form):
    submission_id = forms.UUIDField(widget=forms.HiddenInput)
    carrier = forms.CharField(label='Transporteur', max_length=100, required=False,
                              widget=forms.TextInput(attrs={'placeholder':'Exemple : La Poste / Colissimo'}))
    tracking_number = forms.CharField(label='Numéro de suivi', max_length=120, required=False)
    proof = forms.FileField(label='Photo du justificatif d’expédition', required=False,
        widget=forms.FileInput(attrs={'accept':'image/jpeg,image/png,image/webp'}),
        help_text='JPEG, PNG ou WebP, 5 Mo maximum. Photographiez votre reçu si besoin. Le justificatif reste privé.')
    confirm = forms.BooleanField(label='Je confirme avoir expédié les articles de ce dossier.')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['submission_id'].initial = uuid.uuid4()

    def clean_proof(self):
        from core.catalog_forms import ShopImageField
        upload = self.cleaned_data.get('proof')
        if not upload:
            return None
        if upload.size > 5 * 1024 * 1024:
            raise forms.ValidationError('Le justificatif ne doit pas dépasser 5 Mo.')
        normalized = ShopImageField().clean(upload)
        if normalized.size > 5 * 1024 * 1024:
            raise forms.ValidationError('Le justificatif reste trop volumineux. Choisissez une photo plus petite.')
        return normalized

    def clean(self):
        data = super().clean()
        for key in ('carrier', 'tracking_number'):
            data[key] = ' '.join(data.get(key, '').split())
        if not data.get('tracking_number') and not data.get('proof') and 'proof' not in self.errors:
            raise forms.ValidationError('Ajoutez un numéro de suivi ou une photo du justificatif.')
        return data
