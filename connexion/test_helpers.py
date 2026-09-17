from django.utils import timezone
from .models import EmailVerification


def verify_test_user(user):
    if not user.email:
        user.email=f'test-{user.pk}@example.test'
        user.save(update_fields=['email'])
    EmailVerification.objects.update_or_create(user=user,defaults={'verified_email':user.email.lower(),'verified_at':timezone.now()})
