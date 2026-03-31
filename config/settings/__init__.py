import os

if os.environ.get('DJANGO_SETTINGS_MODULE') == 'config.settings.prod':
    from .prod import *
else:
    from .dev import *