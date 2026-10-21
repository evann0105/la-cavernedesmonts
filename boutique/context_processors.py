from .configuration import configuration


def shop_information(request):
    return {'shop': configuration()}
