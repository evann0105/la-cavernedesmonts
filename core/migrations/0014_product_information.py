from django.db import migrations, models

class Migration(migrations.Migration):
    dependencies = [('core','0013_baby_products_in_gifts')]
    operations = [
        migrations.AddField(model_name='product',name='brand',field=models.CharField('Marque',max_length=100,blank=True)),
        migrations.AddField(model_name='product',name='model_reference',field=models.CharField('Référence du modèle',max_length=160,blank=True)),
        migrations.AddField(model_name='product',name='composition',field=models.TextField('Composition textile',blank=True,help_text='Reprendre les fibres et pourcentages par partie sur l’étiquette ou une fiche technique exacte. Ne pas déduire 100 % d’une simple mention de fibre.')),
        migrations.AddField(model_name='product',name='brand_contact',field=models.TextField('Contact du responsable de la marque',blank=True,help_text='Préciser le rôle, le nom légal, l’adresse postale et le contact électronique. Le responsable de marque ne vaut pas identification automatique du fabricant ou du responsable UE du lot.')),
        migrations.AddField(model_name='product',name='information_sources',field=models.JSONField(default=dict,blank=True,editable=False)),
        migrations.AddField(model_name='product',name='information_review_notes',field=models.TextField('Points restant à contrôler (privé)',blank=True,help_text='Notes visibles uniquement dans la gestion. Mettez-les à jour après vérification de l’étiquette ou du fournisseur.')),
    ]
