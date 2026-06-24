from django.core.management.base import BaseCommand
from django.conf import settings
import subprocess
from datetime import datetime
import os

class Command(BaseCommand):
    help = 'Backup only the database - Dynamically handles Windows Localhost & Linux Production Paths'

    def handle(self, *args, **options):
        # 1. Capture timestamp for filename tracking
        timestamp = datetime.now().strftime("%d%m%Y_%H%M")
        
        # 2. Extract configuration directory setting safely
        raw_backup_dir = getattr(settings, 'BACKUP_DIRECTORY', '/backups')
        
        # 🟢 3. CRITICAL: Normalize path format dynamically to fix Windows vs Linux slash mismatches
        backup_dir = os.path.normpath(raw_backup_dir)
        
        backup_name = f"SMVS_DB_{timestamp}.sql"
        backup_path = os.path.join(backup_dir, backup_name)

        # Ensure destination folder mapping physically exists before execution stream
        os.makedirs(backup_dir, exist_ok=True)

        db_settings = settings.DATABASES['default']

        self.stdout.write(self.style.SUCCESS(f"Creating Database Backup → {backup_name}"))
        self.stdout.write(self.style.SUCCESS(f"Backup Path: {backup_dir}"))

        try:
            # 🟢 4. CRITICAL: Dynamic Host Mapping fallback to support cross-platform networking
            db_host = db_settings.get('HOST', 'localhost')
            if not db_host:  # Fallback if host is defined as empty string in settings
                db_host = 'postgres'

            cmd = [
                'pg_dump',
                '-U', db_settings['USER'],
                '-h', db_host,
                '-p', str(db_settings.get('PORT', 5432)),
                '-F', 'c',                     # custom format (compressed binary)
                '-f', backup_path,
                db_settings['NAME']
            ]

            env = os.environ.copy()
            env['PGPASSWORD'] = db_settings['PASSWORD']

            # Run execution subprocess pipe stream safely
            result = subprocess.run(cmd, env=env, capture_output=True, text=True)

            if result.returncode == 0:                
                self.stdout.write(self.style.SUCCESS(f"✅ Database Backup Created Successfully!"))
                self.stdout.write(self.style.SUCCESS(f"📁 Saved at: {backup_path}"))
                self.stdout.write(self.style.SUCCESS(f"Size: {os.path.getsize(backup_path) / (1024*1024):.2f} MB")) 
            else:
                self.stdout.write(self.style.ERROR(f"❌ DB Backup failed: {result.stderr}"))
                # Raise exception to ensure Django task tracking captures execution failures cleanly
                raise RuntimeError(result.stderr)

        except FileNotFoundError:
            self.stdout.write(self.style.ERROR("❌ 'pg_dump' command not found. Install postgresql-client inside your Docker container."))
            raise
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"❌ Error: {e}"))
            raise