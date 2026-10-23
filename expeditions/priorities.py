"""Human-readable time spent waiting for dispatch; no promised deadline inferred."""


def waiting_time(since, now):
    seconds = max(0, int((now - since).total_seconds()))
    if seconds < 60:
        return 'moins d’une minute'
    if seconds < 3600:
        value, unit = seconds // 60, 'minute'
    elif seconds < 86400:
        value, unit = seconds // 3600, 'heure'
    else:
        value, unit = seconds // 86400, 'jour'
    return f'{value} {unit}{"s" if value > 1 else ""}'
