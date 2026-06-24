from django.core.management.base import BaseCommand
from approval_core.models import ApprovalLevel

class Command(BaseCommand):
    help = 'Creates default Approval Levels if they do not exist'

    def handle(self, *args, **options):
        default_levels = [
            {"level_number": 1, "level_name": "Operator", "description": "First level approver / Document Verifier", "is_active": True},
            {"level_number": 2, "level_name": "MK Sabhya", "description": "MK Sabha Member", "is_active": True},
            {"level_number": 3, "level_name": "3rd Party Verifier", "description": "External verification level", "is_active": True},
            {"level_number": 4, "level_name": "MK Sant 1", "description": "MK Sant 1", "is_active": True},
            {"level_number": 5, "level_name": "MK Sant 2", "description": "MK Sant 2", "is_active": True},
            {"level_number": 6, "level_name": "HDH Guruji", "description": "HDH Guruji - Final Approver", "is_active": True},
        ]

        created_count = 0
        for level_data in default_levels:
            level, created = ApprovalLevel.objects.get_or_create(
                level_number=level_data["level_number"],
                defaults={
                    "level_name": level_data["level_name"],
                    "description": level_data["description"],
                    "is_active": level_data["is_active"],
                }
            )
            if created:
                created_count += 1
                self.stdout.write(self.style.SUCCESS(f'✓ Created level: {level.level_number} - {level.level_name}'))
            else:
                self.stdout.write(f'• Level already exists: {level.level_number} - {level.level_name}')

        self.stdout.write(self.style.SUCCESS(f'\n✅ Default Approval Levels setup completed. Created {created_count} new levels.'))