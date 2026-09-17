from django.contrib.auth.backends import ModelBackend
from django.contrib.auth import get_user_model


class EmailOrUsernameBackend(ModelBackend):
    def authenticate(self, request, username=None, password=None, **kwargs):
        model = get_user_model()
        identifier = username or kwargs.get(model.USERNAME_FIELD, '')
        if not model.objects.filter(username=identifier).exists():
            matches = model.objects.filter(email__iexact=identifier)
            if matches.count() == 1:
                identifier = matches.first().username
        return super().authenticate(request, username=identifier, password=password, **kwargs)
