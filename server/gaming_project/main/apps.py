import os
import sys

from django.apps import AppConfig


class MainConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'gaming_project.main'
    label = 'gaming_main'

    def ready(self):
        # AppConfig.ready() fires for every management command (test, migrate,
        # collectstatic, shell, ...), not just the actual serving process - starting a
        # background scheduler that sends real push notifications during `manage.py
        # test`'s 200+ test suite (or during Render's build-step migrate/collectstatic)
        # would be a real incident, not just noise. Only start it for: `runserver`'s
        # actual reloaded child (RUN_MAIN distinguishes it from the autoreload parent
        # watcher, which imports everything too but never serves anything), or when
        # Django is booted directly via WSGI in production (gunicorn's argv has no
        # manage.py, so it isn't a management command at all).
        argv = sys.argv
        is_manage_command = len(argv) > 0 and 'manage.py' in argv[0]
        command = argv[1] if is_manage_command and len(argv) > 1 else None

        if is_manage_command and command != 'runserver':
            return
        if command == 'runserver' and os.environ.get('RUN_MAIN') != 'true':
            return

        from .Handlers.scheduler import start_scheduler
        start_scheduler()
