from django.contrib.auth.backends import ModelBackend
from django.contrib.auth import get_user_model


class EmailOrUsernameBackend(ModelBackend):
    def authenticate(self, request, username=None, password=None, **kwargs):
        model = get_user_model()
        identifier = username or kwargs.get(model.USERNAME_FIELD, '')
        from .security import resolve_login_identifier
        user = resolve_login_identifier(identifier)
        if user is not None:
            identifier = user.username
        return super().authenticate(request, username=identifier, password=password, **kwargs)
