import os
import tarfile
import shutil
import subprocess
from django.core.management.base import BaseCommand
from django.conf import settings
from django.db import connection

class Command(BaseCommand):
    help = 'Restores the project media files and database from a .tar.gz backup'

    def add_arguments(self, parser):
        parser.add_argument('backup_path', type=str, help='Path to the .tar.gz backup file')

    def handle(self, *args, **options):
        backup_path = options['backup_path']
        temp_extract_dir = os.path.join(settings.MEDIA_ROOT, 'temp_restore_extract')

        try:
            self.stdout.write(self.style.NOTICE(f"Starting restoration from: {backup_path}"))

            # 1. ✅ PYTHON-BASED EXTRACTION (No system 'tar' required)
            if not os.path.exists(temp_extract_dir):
                os.makedirs(temp_extract_dir)

            with tarfile.open(backup_path, "r:gz") as tar:
                self.stdout.write("Extracting files...")
                tar.extractall(path=temp_extract_dir)

            # 2. RESTORE MEDIA FILES
            # Assumes backup has an 'app/media' or similar folder structure
            extracted_media = os.path.join(temp_extract_dir, 'app', 'media') 
            if os.path.exists(extracted_media):
                self.stdout.write("Restoring media files...")
                # Clear existing media and replace
                shutil.rmtree(settings.MEDIA_ROOT)
                shutil.copytree(extracted_media, settings.MEDIA_ROOT)

            # 3. RESTORE DATABASE (.sql file)
            # Assumes a file named 'db_backup.sql' is inside the archive
            sql_file = os.path.join(temp_extract_dir, 'db_backup.sql')
            if os.path.exists(sql_file):
                self.stdout.write("Restoring database...")
                self.restore_database(sql_file)

            self.stdout.write(self.style.SUCCESS("✅ Restoration completed successfully!"))

        except Exception as e:
            self.stdout.write(self.style.ERROR(f"❌ Restore failed: {str(e)}"))
        finally:
            # Cleanup
            if os.path.exists(temp_extract_dir):
                shutil.rmtree(temp_extract_dir)

    def restore_database(self, sql_path):
        """Generic database restoration logic"""
        db_conf = settings.DATABASES['default']
        
        # If using SQLite
        if db_conf['ENGINE'] == 'django.db.backends.sqlite3':
            db_path = db_conf['NAME']
            shutil.copy(sql_path, db_path) # For SQLite, the backup is often just the .sqlite3 file
        
        # If using PostgreSQL
        elif db_conf['ENGINE'] == 'django.db.backends.postgresql':
            env = os.environ.copy()
            env['PGPASSWORD'] = db_conf['PASSWORD']
            subprocess.run([
                'psql', '-h', db_conf['HOST'], '-U', db_conf['USER'], '-d', db_conf['NAME'], '-f', sql_path
            ], env=env, check=True)