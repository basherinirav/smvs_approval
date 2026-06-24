from django.core.management.base import BaseCommand
from django.conf import settings
import subprocess
from datetime import datetime
import os

class Command(BaseCommand):
    help = 'Create full project backup (code + media) - Linux/Docker compatible'

    def handle(self, *args, **options):
        timestamp = datetime.now().strftime("%d%m%Y_%H%M")
        backup_dir = getattr(settings, 'BACKUP_DIRECTORY', '/backups')
        project_path = settings.BASE_DIR
        backup_name = f"SMVS_Approval_Full_{timestamp}.tar.gz"
        backup_path = os.path.join(backup_dir, backup_name)

        os.makedirs(backup_dir, exist_ok=True)

        self.stdout.write(self.style.SUCCESS(f"Creating Full Project Backup → {backup_name}"))
        self.stdout.write(self.style.SUCCESS(f"Backup Path: {backup_dir}"))

        try:
            # Use tar.gz - reliable in Linux/Docker
            cmd = [
                'tar', '-czf', backup_path,
                '--exclude=*.pyc',
                '--exclude=__pycache__',
                '--exclude=media',           # optional: exclude large media if not needed
                '-C', str(project_path.parent),
                project_path.name
            ]

            result = subprocess.run(cmd, capture_output=True, text=True)

            if result.returncode == 0:
                self.stdout.write(self.style.SUCCESS(f"✅ Full Backup Created Successfully!"))
                self.stdout.write(self.style.SUCCESS(f"📁 Saved at: {backup_path}"))
                self.stdout.write(self.style.SUCCESS(f"Size: {os.path.getsize(backup_path) / (1024*1024):.2f} MB"))
            else:
                self.stdout.write(self.style.ERROR(f"❌ Backup failed: {result.stderr}"))
                self.stdout.write(self.style.ERROR(f"Command output: {result.stdout}"))

        except FileNotFoundError:
            self.stdout.write(self.style.ERROR("❌ 'tar' command not found. Make sure it's installed in the container."))
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"❌ Error during backup: {e}"))