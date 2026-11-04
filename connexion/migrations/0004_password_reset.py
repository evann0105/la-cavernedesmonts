import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models
import config.encrypted_fields


class Migration(migrations.Migration):
    dependencies = [('connexion', '0003_alter_emailverification_verified_email'), migrations.swappable_dependency(settings.AUTH_USER_MODEL)]
    operations = [
        migrations.CreateModel(name='PasswordResetMail', fields=[
            ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
            ('kind', models.CharField(choices=[('reset', 'Récupération'), ('changed', 'Mot de passe modifié')], default='reset', max_length=12)),
            ('recipient', config.encrypted_fields.EncryptedEmailField()),
            ('recipient_hash', models.CharField(db_index=True, max_length=64)),
            ('origin', models.URLField(max_length=254)),
            ('payload', config.encrypted_fields.EncryptedJSONField(blank=True, default=dict)),
            ('state', models.CharField(choices=[('pending', 'En attente'), ('sending', 'Envoi en cours'), ('sent', 'Accepté'), ('skipped', 'Sans envoi'), ('failed', 'Échec')], db_index=True, default='pending', max_length=12)),
            ('created_at', models.DateTimeField(db_index=True, default=django.utils.timezone.now)),
            ('claimed_at', models.DateTimeField(blank=True, null=True)),
            ('claim_id', models.UUIDField(blank=True, null=True)),
            ('attempts', models.PositiveSmallIntegerField(default=0)),
            ('sent_at', models.DateTimeField(blank=True, null=True)),
            ('user', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to=settings.AUTH_USER_MODEL)),
        ]),
        migrations.CreateModel(name='PasswordResetState', fields=[
            ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
            ('token_digest', models.CharField(blank=True, max_length=64)),
            ('identity_digest', models.CharField(blank=True, max_length=64)),
            ('issued_at', models.DateTimeField(blank=True, null=True)),
            ('mail', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to='connexion.passwordresetmail')),
            ('user', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='password_reset_state', to=settings.AUTH_USER_MODEL)),
        ]),
    ]
