import csv
import json
from django.core.management.base import BaseCommand
from django.db import transaction
from approval_core.models import Country, Zone, Center, Department, PostMaster, PostEmailMapping


class Command(BaseCommand):
    help = "Import master data (Country, Zone, Center, Department) from CSV files"

    def add_arguments(self, parser):
        parser.add_argument('--country', type=str, help='CSV file path for countries')
        parser.add_argument('--zone', type=str, help='CSV file path for zones')
        parser.add_argument('--center', type=str, help='CSV file path for centers')
        parser.add_argument('--department', type=str, help='CSV file path for departments')
        parser.add_argument('--sample', action='store_true', help='Create sample data')

    def handle(self, *args, **options):
        if options['sample']:
            self.create_sample_data()
            return

        if options['country']:
            self.import_countries(options['country'])
        if options['zone']:
            self.import_zones(options['zone'])
        if options['center']:
            self.import_centers(options['center'])
        if options['department']:
            self.import_departments(options['department'])

    @transaction.atomic
    def import_countries(self, filepath):
        """Import countries from CSV"""
        self.stdout.write(f"Importing countries from {filepath}...")
        count = 0

        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    country, created = Country.objects.update_or_create(
                        code=row['code'],
                        defaults={
                            'name': row['name'],
                            'description': row.get('description', ''),
                            'is_active': row.get('is_active', 'true').lower() == 'true'
                        }
                    )
                    count += 1
                    if created:
                        self.stdout.write(f"  Created: {country}")
                    else:
                        self.stdout.write(f"  Updated: {country}")

            self.stdout.write(self.style.SUCCESS(f"[DONE] Imported {count} countries"))
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"Error: {e}"))

    @transaction.atomic
    def import_zones(self, filepath):
        """Import zones from CSV"""
        self.stdout.write(f"Importing zones from {filepath}...")
        count = 0

        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    try:
                        country = Country.objects.get(code=row['country_code'])
                        zone, created = Zone.objects.update_or_create(
                            country=country,
                            code=row['code'],
                            defaults={
                                'name': row['name'],
                                'description': row.get('description', ''),
                                'is_active': row.get('is_active', 'true').lower() == 'true'
                            }
                        )
                        count += 1
                        if created:
                            self.stdout.write(f"  Created: {zone}")
                        else:
                            self.stdout.write(f"  Updated: {zone}")
                    except Country.DoesNotExist:
                        self.stdout.write(
                            self.style.WARNING(f"  Skipped {row['code']}: Country {row['country_code']} not found")
                        )

            self.stdout.write(self.style.SUCCESS(f"[DONE] Imported {count} zones"))
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"Error: {e}"))

    @transaction.atomic
    def import_centers(self, filepath):
        """Import centers from CSV"""
        self.stdout.write(f"Importing centers from {filepath}...")
        count = 0

        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    try:
                        zone = Zone.objects.get(code=row['zone_code'], country__code=row['country_code'])
                        center, created = Center.objects.update_or_create(
                            zone=zone,
                            code=row['code'],
                            defaults={
                                'name': row['name'],
                                'address': row.get('address', ''),
                                'city': row.get('city', ''),
                                'state': row.get('state', ''),
                                'pincode': row.get('pincode', ''),
                                'is_active': row.get('is_active', 'true').lower() == 'true'
                            }
                        )
                        count += 1
                        if created:
                            self.stdout.write(f"  Created: {center}")
                        else:
                            self.stdout.write(f"  Updated: {center}")
                    except Zone.DoesNotExist:
                        self.stdout.write(
                            self.style.WARNING(f"  Skipped {row['code']}: Zone {row['zone_code']} not found")
                        )

            self.stdout.write(self.style.SUCCESS(f"[DONE] Imported {count} centers"))
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"Error: {e}"))

    @transaction.atomic
    def import_departments(self, filepath):
        """Import departments from CSV"""
        self.stdout.write(f"Importing departments from {filepath}...")
        count = 0

        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    try:
                        country = Country.objects.get(code=row['country_code'])
                        center = None
                        if row.get('center_code'):
                            center = Center.objects.get(code=row['center_code'])

                        department, created = Department.objects.update_or_create(
                            country=country,
                            code=row['code'],
                            defaults={
                                'name': row['name'],
                                'description': row.get('description', ''),
                                'center': center,
                                'is_active': row.get('is_active', 'true').lower() == 'true'
                            }
                        )
                        count += 1
                        if created:
                            self.stdout.write(f"  Created: {department}")
                        else:
                            self.stdout.write(f"  Updated: {department}")
                    except (Country.DoesNotExist, Center.DoesNotExist) as e:
                        self.stdout.write(
                            self.style.WARNING(f"  Skipped {row['code']}: {e}")
                        )

            self.stdout.write(self.style.SUCCESS(f"[DONE] Imported {count} departments"))
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"Error: {e}"))

    @transaction.atomic
    def create_sample_data(self):
        """Create sample master data"""
        self.stdout.write("Creating sample master data...")

        # Create Country
        india, _ = Country.objects.get_or_create(
            code="IND",
            defaults={'name': 'India', 'is_active': True}
        )
        self.stdout.write(f"  Created Country: {india}")

        # Create Zones
        zone_south, _ = Zone.objects.get_or_create(
            country=india,
            code="Z_SOUTH",
            defaults={'name': 'South Zone', 'is_active': True}
        )
        zone_north, _ = Zone.objects.get_or_create(
            country=india,
            code="Z_NORTH",
            defaults={'name': 'North Zone', 'is_active': True}
        )
        self.stdout.write(f"  Created Zones: {zone_south}, {zone_north}")

        # Create Centers
        center_bangalore, _ = Center.objects.get_or_create(
            zone=zone_south,
            code="C_BNG",
            defaults={
                'name': 'Bangalore Center',
                'city': 'Bangalore',
                'state': 'Karnataka',
                'pincode': '560001',
                'is_active': True
            }
        )
        center_delhi, _ = Center.objects.get_or_create(
            zone=zone_north,
            code="C_DEL",
            defaults={
                'name': 'Delhi Center',
                'city': 'Delhi',
                'state': 'Delhi',
                'pincode': '110001',
                'is_active': True
            }
        )
        self.stdout.write(f"  Created Centers: {center_bangalore}, {center_delhi}")

        # Create Departments
        dept_finance, _ = Department.objects.get_or_create(
            country=india,
            code="D_FIN",
            defaults={
                'name': 'Finance Department',
                'center': center_bangalore,
                'is_active': True
            }
        )
        dept_admin, _ = Department.objects.get_or_create(
            country=india,
            code="D_ADM",
            defaults={
                'name': 'Admin Department',
                'center': center_delhi,
                'is_active': True
            }
        )
        dept_operations, _ = Department.objects.get_or_create(
            country=india,
            code="D_OPS",
            defaults={
                'name': 'Operations Department',
                'is_active': True
            }
        )
        self.stdout.write(f"  Created Departments: {dept_finance}, {dept_admin}, {dept_operations}")

        # Create PostMaster
        center_email_post, _ = PostMaster.objects.get_or_create(
            post_type="center",
            role_name="Center Email",
            defaults={'is_active': True}
        )
        center_sant_post, _ = PostMaster.objects.get_or_create(
            post_type="center",
            role_name="Center Sant Email",
            defaults={'is_active': True}
        )
        dept_hod_post, _ = PostMaster.objects.get_or_create(
            post_type="department",
            role_name="Department HOD",
            defaults={'is_active': True}
        )
        self.stdout.write(f"  Created Posts: {center_email_post}, {center_sant_post}, {dept_hod_post}")

        # Create PostEmailMappings
        PostEmailMapping.objects.get_or_create(
            post=center_email_post,
            mapping_type="center",
            center=center_bangalore,
            defaults={
                'email': 'bangalore@center.org',
                'person_name': 'Bangalore Center Manager',
                'is_primary': True,
                'is_active': True
            }
        )
        PostEmailMapping.objects.get_or_create(
            post=center_sant_post,
            mapping_type="center",
            center=center_delhi,
            defaults={
                'email': 'delhi.sant@center.org',
                'person_name': 'Delhi Sant',
                'is_primary': True,
                'is_active': True
            }
        )
        PostEmailMapping.objects.get_or_create(
            post=dept_hod_post,
            mapping_type="department",
            department=dept_finance,
            defaults={
                'email': 'finance.hod@org.in',
                'person_name': 'Finance HOD',
                'is_primary': True,
                'is_active': True
            }
        )
        self.stdout.write("  Created Email Mappings")

        # Create Email Notification Templates
        from approval_core.models import EmailNotificationTemplate

        templates = [
            {
                'template_name': 'Submit Notification',
                'subject': 'Form {{form_number}} - Submitted',
                'body': 'Dear {{recipient_name}},\n\nA new form {{form_number}} has been submitted for approval.\n\nBest regards,\nSMVS System',
                'event_type': 'submit',
                'context_model': 'approval_form'
            },
            {
                'template_name': 'Approval Request',
                'subject': 'Action Required: {{form_number}} - {{subject}}',
                'body': 'Dear {{approver_name}},\n\nForm {{form_number}} requires your approval.\nAmount: {{amount}}\n\n{{approval_link}}\n\nBest regards,\nSMVS System',
                'event_type': 'pending_approval',
                'context_model': 'approval_form'
            },
            {
                'template_name': 'Approved Notification',
                'subject': 'Approved: {{form_number}} - {{subject}}',
                'body': 'Dear {{submitter_name}},\n\nYour form {{form_number}} has been approved!\n\nBest regards,\nSMVS System',
                'event_type': 'approved',
                'context_model': 'approval_form'
            },
            {
                'template_name': 'Rejected Notification',
                'subject': 'Rejected: {{form_number}} - {{subject}}',
                'body': 'Dear {{submitter_name}},\n\nYour form {{form_number}} has been rejected.\nReason: {{reason}}\n\nBest regards,\nSMVS System',
                'event_type': 'rejected',
                'context_model': 'approval_form'
            }
        ]

        for template in templates:
            EmailNotificationTemplate.objects.get_or_create(
                template_name=template['template_name'],
                defaults=template
            )

        self.stdout.write("  Created Email Notification Templates")

        self.stdout.write(self.style.SUCCESS("[DONE] Sample master data created successfully!"))
