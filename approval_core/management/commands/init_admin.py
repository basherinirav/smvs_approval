from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model

class Command(BaseCommand):
    help = 'Safely checks and auto-creates the production tracking administrator account'

    def handle(self, *args, **options):
        User = get_user_model()
        username = 'admin'
        email = 'manjuripatra@in.smvs.org'
        password = 'Admin@222429'

        if not User.objects.filter(username=username).exists():
            User.objects.create_superuser(username=username, email=email, password=password)
            self.stdout.write(self.style.SUCCESS('✅ Production Superuser account auto-created successfully!'))
        else:
            self.stdout.write(self.style.NOTICE('ℹ️ Superuser account matching config profile already exists. Skipping.'))