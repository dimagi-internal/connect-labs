from django.apps import AppConfig


class WebAppConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "connect_labs.web"

    def ready(self):
        # connect_labs.utils is a package of helpers, not an installed app, so
        # its outbound-email system check needs a host AppConfig to register
        # from. `web` is in LOCAL_APPS for every settings module, which makes it
        # the one place guaranteed to run. See connect_labs/utils/email.py.
        from django.core.checks import register

        from connect_labs.utils.email import check_labs_email

        register(check_labs_email)
