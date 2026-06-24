from django.contrib import admin
from django.utils.html import format_html
from django.urls import path, reverse
from django.shortcuts import render, redirect
from import_export.admin import ExportMixin
from approval_core.models import *
from approval_core.models import ExchangeRate
from approval_core.import_export import (
    ImportExportHelper,
    COUNTRIES_SAMPLE,
    ZONES_SAMPLE,
    CENTERS_SAMPLE,
    DEPARTMENTS_SAMPLE,
    POSTS_SAMPLE,
    EMAIL_MAPPINGS_SAMPLE,
)


# ==================== SECTION 1: MASTER DATA ====================

@admin.register(Country)
class CountryAdmin(ExportMixin, admin.ModelAdmin):
    list_display = ("code", "name", 'phone_code', "currency_code", "currency_symbol", "zone_count", "is_active", "created_at")
    list_filter = ("is_active", "created_at")
    search_fields = ("code", "name")
    readonly_fields = ("created_at", "updated_at")
    change_list_template = "admin/approval_core/country/change_list.html"

    fieldsets = (
        ("Country Information", {
            "fields": ("code", "name", "description")
        }),
        ("Currency", {
            "fields": ("currency_code", "currency_symbol"),
            "description": "Set the local currency for this country. "
                           "Examples — India: INR / ₹ | USA: USD / $ | UK: GBP / £ | UAE: AED / د.إ | "
                           "Canada: CAD / C$ | Australia: AUD / A$"
        }),
        ("Status", {
            "fields": ("is_active",)
        }),
        ("Timestamps", {
            "fields": ("created_at", "updated_at"),
            "classes": ("collapse",)
        }),
    )

    def zone_count(self, obj):
        count = obj.zones.count()
        return format_html(f'<span style="background-color: #17a2b8; color: white; padding: 3px 8px; border-radius: 3px;">{count}</span>')
    zone_count.short_description = "Zones"

    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path('import/', self.admin_site.admin_view(self.import_countries), name='country_import'),
            path('export-sample/', self.admin_site.admin_view(self.export_sample), name='country_export_sample'),
        ]
        return custom_urls + urls

    def import_countries(self, request):
        if request.method == 'POST' and request.FILES.get('file'):
            file = request.FILES['file']
            try:
                if file.name.endswith('.csv'):
                    data = ImportExportHelper.parse_csv_file(file)
                elif file.name.endswith('.xlsx'):
                    data = ImportExportHelper.parse_xlsx_file(file)
                else:
                    self.message_user(request, "Please upload CSV or XLSX file", level='error')
                    return render(request, 'admin/import_form.html', {'title': 'Import Countries', 'module': 'Countries'})
                
                imported = 0
                for row in data:
                    # 🟢 Standardize data lookup to handle both "phone_code" and "Phone Code"
                    phone = row.get('phone_code') or row.get('Phone Code') or row.get('PHONE_CODE', '')
                    curr_code = row.get('currency_code') or row.get('Currency Code') or row.get('CURRENCY_CODE', '')
                    curr_sym = row.get('currency_symbol') or row.get('Currency Symbol') or row.get('CURRENCY_SYMBOL', '')
                    active_val = row.get('is_active') or row.get('Is Active') or '1'

                    Country.objects.update_or_create(
                        code=row['code'],
                        defaults={
                            'name': row['name'], 
                            'description': row.get('description', '') or row.get('Description', ''),
                            'phone_code': phone,
                            'currency_code': curr_code,
                            'currency_symbol': curr_sym,
                            'is_active': str(active_val).lower() in ['1', 'true', 'yes']
                        }
                    )
                    imported += 1
                self.message_user(request, f"✓ Successfully imported {imported} countries")
                return redirect(request.path)
            except Exception as e:
                self.message_user(request, f"✗ Error importing: {str(e)}", level='error')
        return render(request, 'admin/import_form.html', {
            'title': 'Import Countries',
            'module': 'Countries',
            'sample_csv': reverse('admin:country_export_sample')
        })
    
    def export_sample(self, request):
        file_format = request.GET.get('format', 'csv')
        filename = f'countries_sample.{file_format}'
        
        if file_format == 'xlsx':
            return ImportExportHelper.generate_xlsx_response(
                filename, 'Countries',
                COUNTRIES_SAMPLE['headers'],
                COUNTRIES_SAMPLE['rows']
            )
        else:
            return ImportExportHelper.generate_csv_response(
                filename,
                COUNTRIES_SAMPLE['headers'],
                COUNTRIES_SAMPLE['rows']
            )


@admin.register(Zone)
class ZoneAdmin(ExportMixin, admin.ModelAdmin):
    list_display = ("code", "name", "country", "center_count", "is_active", "created_at")
    list_filter = ("is_active", "country", "created_at")
    search_fields = ("code", "name", "country__name")
    readonly_fields = ("created_at", "updated_at")
    change_list_template = "admin/approval_core/zone/change_list.html"

    fieldsets = (
        ("Zone Information", {
            "fields": ("country", "code", "name", "description")
        }),
        ("Status", {
            "fields": ("is_active",)
        }),
        ("Timestamps", {
            "fields": ("created_at", "updated_at"),
            "classes": ("collapse",)
        }),
    )

    def center_count(self, obj):
        count = obj.centers.count()
        return format_html(f'<span style="background-color: #28a745; color: white; padding: 3px 8px; border-radius: 3px;">{count}</span>')
    center_count.short_description = "Centers"

    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path('import/', self.admin_site.admin_view(self.import_zones), name='zone_import'),
            path('export-sample/', self.admin_site.admin_view(self.export_sample), name='zone_export_sample'),
        ]
        return custom_urls + urls

    def import_zones(self, request):
        if request.method == 'POST' and request.FILES.get('file'):
            file = request.FILES['file']
            try:
                if file.name.endswith('.csv'):
                    data = ImportExportHelper.parse_csv_file(file)
                elif file.name.endswith('.xlsx'):
                    data = ImportExportHelper.parse_xlsx_file(file)
                else:
                    self.message_user(request, "Please upload CSV or XLSX file", level='error')
                    return render(request, 'admin/import_form.html', {'title': 'Import Zones', 'module': 'Zones'})
                
                imported = 0
                skipped = 0
                for row in data:
                    # 🟢 1. Extract inputs flexibly supporting multiple variations
                    country_identifier = row.get('country_code') or row.get('country') or row.get('Country')
                    code_val = row.get('code') or row.get('Code')
                    name_val = row.get('name') or row.get('Name')
                    desc_val = row.get('description') or row.get('Description') or ''

                    if not country_identifier or not code_val or not name_val:
                        skipped += 1
                        continue

                    # 🟢 2. Smart Country Resolution (Handles both ID numbers and Alpha Codes)
                    country = None
                    try:
                        if str(country_identifier).strip().isdigit():
                            # If it's a numeric ID (from standard database exports)
                            country = Country.objects.get(id=int(country_identifier))
                        else:
                            # If it's a string code like 'IND', 'AUS' (from manual samples)
                            country = Country.objects.get(code=str(country_identifier).strip().upper())
                    except Country.DoesNotExist:
                        skipped += 1
                        continue

                    # 🟢 3. Perform the update or create cleanly
                    if country:
                        Zone.objects.update_or_create(
                            code=str(code_val).strip(),
                            defaults={
                                'country': country,
                                'name': str(name_val).strip(),
                                'description': str(desc_val).strip()
                            }
                        )
                        imported += 1
                        
                self.message_user(request, f"✓ Successfully imported {imported} zones. (Skipped {skipped} unmatched rows)")
                return redirect(request.path)
                
            except Exception as e:
                self.message_user(request, f"❌ Error importing: {str(e)}", level='error')
    
    def export_sample(self, request):
        file_format = request.GET.get('format', 'csv')
        filename = f'zones_sample.{file_format}'
        
        if file_format == 'xlsx':
            return ImportExportHelper.generate_xlsx_response(
                filename, 'Zones',
                ZONES_SAMPLE['headers'],
                ZONES_SAMPLE['rows']
            )
        else:
            return ImportExportHelper.generate_csv_response(
                filename,
                ZONES_SAMPLE['headers'],
                ZONES_SAMPLE['rows']
            )


@admin.register(Center)
class CenterAdmin(ExportMixin, admin.ModelAdmin):
    list_display = ("code", "name", "zone", "city", "department_count", "is_active", "created_at")
    list_filter = ("is_active", "zone__country", "zone", "city", "created_at")
    search_fields = ("code", "name", "city", "state", "zone__name")
    readonly_fields = ("created_at", "updated_at")
    change_list_template = "admin/approval_core/center/change_list.html"

    fieldsets = (
        ("Center Information", {
            "fields": ("zone", "code", "name")
        }),
        ("Location Details", {
            "fields": ("address", "city", "state", "pincode")
        }),
        ("Status", {
            "fields": ("is_active",)
        }),
        ("Timestamps", {
            "fields": ("created_at", "updated_at"),
            "classes": ("collapse",)
        }),
    )

    def department_count(self, obj):
        count = obj.department_assignments.count()
        return format_html(f'<span style="background-color: #ffc107; color: black; padding: 3px 8px; border-radius: 3px;">{count}</span>')
    department_count.short_description = "Depts"

    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path('import/', self.admin_site.admin_view(self.import_centers), name='center_import'),
            path('export-sample/', self.admin_site.admin_view(self.export_sample), name='center_export_sample'),
        ]
        return custom_urls + urls

    def import_centers(self, request):
        if request.method == 'POST' and request.FILES.get('file'):
            file = request.FILES['file']
            try:
                data = ImportExportHelper.parse_csv_file(file) if file.name.endswith('.csv') else ImportExportHelper.parse_xlsx_file(file)
                imported, skipped = 0, 0
                for row in data:
                    zone_identifier = row.get('zone_code') or row.get('zone') or row.get('Zone')
                    code_val = row.get('code') or row.get('Code')
                    name_val = row.get('name') or row.get('Name')

                    if not zone_identifier or not code_val or not name_val:
                        skipped += 1
                        continue

                    try:
                        if str(zone_identifier).strip().isdigit():
                            zone = Zone.objects.get(id=int(zone_identifier))
                        else:
                            zone = Zone.objects.get(code=str(zone_identifier).strip())
                    except Zone.DoesNotExist:
                        skipped += 1
                        continue

                    Center.objects.update_or_create(
                        code=str(code_val).strip(),
                        defaults={
                            'zone': zone,
                            'name': str(name_val).strip(),
                            'address': row.get('address', '') or row.get('Address', ''),
                            'city': row.get('city', '') or row.get('City', ''),
                            'state': row.get('state', '') or row.get('State', ''),
                            'pincode': row.get('pincode', '') or row.get('Pincode', '')
                        }
                    )
                    imported += 1
                self.message_user(request, f"✓ Imported {imported} centers. (Skipped {skipped})")
                return redirect(request.path)
            except Exception as e:
                self.message_user(request, f"❌ Error: {str(e)}", level='error')
    
    def export_sample(self, request):
        file_format = request.GET.get('format', 'csv')
        filename = f'centers_sample.{file_format}'
        
        if file_format == 'xlsx':
            return ImportExportHelper.generate_xlsx_response(
                filename, 'Centers',
                CENTERS_SAMPLE['headers'],
                CENTERS_SAMPLE['rows']
            )
        else:
            return ImportExportHelper.generate_csv_response(
                filename,
                CENTERS_SAMPLE['headers'],
                CENTERS_SAMPLE['rows']
            )


@admin.register(Department)
class DepartmentAdmin(ExportMixin, admin.ModelAdmin):
    list_display = ("code", "name", "country", "center_name", "email_count", "requires_center_badge", "is_active", "created_at")
    list_filter = ("is_active", "country", "center", "created_at")
    search_fields = ("code", "name", "country__name", "center__name")
    readonly_fields = ("created_at", "updated_at")
    change_list_template = "admin/approval_core/department/change_list.html"

    fieldsets = (
        ("Department Information", {
            "fields": ("country", "code", "name", "description")
        }),
        ("Center Assignment", {
            "fields": ("center",),
            "description": "Optional: Assign to specific center"
        }),
        ("Center Selection Setting", {
            "fields": ("requires_center_selection",),
            "description": "If enabled, end users in this department must select a center when creating a form (e.g. Nirman dept)"
        }),
        ("Status", {
            "fields": ("is_active",)
        }),
        ("Timestamps", {
            "fields": ("created_at", "updated_at"),
            "classes": ("collapse",)
        }),
    )

    def center_name(self, obj):
        if obj.center:
            return obj.center.name
        return "—"
    center_name.short_description = "Center"

    def email_count(self, obj):
        count = obj.email_mappings.count()
        return format_html(f'<span style="background-color: #e83e8c; color: white; padding: 3px 8px; border-radius: 3px;">{count}</span>')
    email_count.short_description = "Emails"

    def requires_center_badge(self, obj):
        if obj.requires_center_selection:
            return format_html(
                '<span style="background-color:#28a745;color:white;padding:3px 8px;border-radius:3px;">'
                '<i class="fas fa-check"></i> Yes</span>'
            )
        return format_html(
            '<span style="background-color:#6c757d;color:white;padding:3px 8px;border-radius:3px;">No</span>'
        )
    requires_center_badge.short_description = "Needs Center?"

    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path('import/', self.admin_site.admin_view(self.import_departments), name='department_import'),
            path('export-sample/', self.admin_site.admin_view(self.export_sample), name='department_export_sample'),
        ]
        return custom_urls + urls

    def import_departments(self, request):
        if request.method == 'POST' and request.FILES.get('file'):
            file = request.FILES['file']
            try:
                data = ImportExportHelper.parse_csv_file(file) if file.name.endswith('.csv') else ImportExportHelper.parse_xlsx_file(file)
                imported, skipped = 0, 0
                for row in data:
                    country_identifier = row.get('country_code') or row.get('country')
                    center_identifier = row.get('center_code') or row.get('center')
                    code_val = row.get('code') or row.get('Code')
                    name_val = row.get('name') or row.get('Name')

                    try:
                        # Match Country
                        if str(country_identifier).strip().isdigit():
                            country = Country.objects.get(id=int(country_identifier))
                        else:
                            country = Country.objects.get(code=str(country_identifier).strip().upper())
                        
                        # Match Optional Center
                        center = None
                        if center_identifier and str(center_identifier).strip() not in ['', 'None', 'NaN']:
                            if str(center_identifier).strip().isdigit():
                                center = Center.objects.get(id=int(center_identifier))
                            else:
                                center = Center.objects.get(code=str(center_identifier).strip())
                    except (Country.DoesNotExist, Center.DoesNotExist):
                        skipped += 1
                        continue

                    Department.objects.update_or_create(
                        code=str(code_val).strip(),
                        defaults={
                            'country': country,
                            'center': center,
                            'name': str(name_val).strip(),
                            'description': row.get('description', '') or row.get('Description', ''),
                            'requires_center_selection': str(row.get('requires_center_selection', '')).lower() in ['1', 'true', 'yes']
                        }
                    )
                    imported += 1
                self.message_user(request, f"✓ Imported {imported} departments. (Skipped {skipped})")
                return redirect(request.path)
            except Exception as e:
                self.message_user(request, f"❌ Error: {str(e)}", level='error')
    
    def export_sample(self, request):
        file_format = request.GET.get('format', 'csv')
        filename = f'departments_sample.{file_format}'
        
        if file_format == 'xlsx':
            return ImportExportHelper.generate_xlsx_response(
                filename, 'Departments',
                DEPARTMENTS_SAMPLE['headers'],
                DEPARTMENTS_SAMPLE['rows']
            )
        else:
            return ImportExportHelper.generate_csv_response(
                filename,
                DEPARTMENTS_SAMPLE['headers'],
                DEPARTMENTS_SAMPLE['rows']
            )


@admin.register(PostMaster)
class PostAdmin(ExportMixin, admin.ModelAdmin):
    list_display = ("role_name", "post_type_badge", "mapping_count", "is_active", "created_at")
    list_filter = ("post_type", "is_active", "created_at")
    search_fields = ("role_name",)
    readonly_fields = ("created_at", "updated_at")
    change_list_template = "admin/approval_core/postmaster/change_list.html"

    fieldsets = (
        ("Post Information", {
            "fields": ("post_type", "role_name", "description")
        }),
        ("Status", {
            "fields": ("is_active",)
        }),
        ("Timestamps", {
            "fields": ("created_at", "updated_at"),
            "classes": ("collapse",)
        }),
    )

    def post_type_badge(self, obj):
        color = "#007bff" if obj.post_type == "center" else "#6f42c1"
        label = obj.get_post_type_display()
        return format_html(f'<span style="background-color: {color}; color: white; padding: 3px 6px; border-radius: 3px;">{label}</span>')
    post_type_badge.short_description = "Type"

    def mapping_count(self, obj):
        count = obj.email_mappings.count()
        return format_html(f'<span style="background-color: #20c997; color: white; padding: 3px 8px; border-radius: 3px;">{count}</span>')
    mapping_count.short_description = "Mapped"
    
    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path('import/', self.admin_site.admin_view(self.import_posts), name='post_import'),
            path('export-sample/', self.admin_site.admin_view(self.export_sample), name='post_export_sample'),
        ]
        return custom_urls + urls

    def import_posts(self, request):
        if request.method == 'POST' and request.FILES.get('file'):
            file = request.FILES['file']
            try:
                if file.name.endswith('.csv'):
                    data = ImportExportHelper.parse_csv_file(file)
                elif file.name.endswith('.xlsx'):
                    data = ImportExportHelper.parse_xlsx_file(file)
                else:
                    self.message_user(request, "Please upload CSV or XLSX file", level='error')
                    return render(request, 'admin/import_form.html', {'title': 'Import Posts', 'module': 'Posts'})
                
                imported = 0
                skipped = 0
                for row in data:
                    # 🟢 Handle both lowercase and capitalized header strings seamlessly
                    post_type_val = row.get('post_type') or row.get('Post Type')
                    role_name_val = row.get('role_name') or row.get('Role Name')
                    desc_val = row.get('description') or row.get('Description') or ''

                    if post_type_val and role_name_val:
                        PostMaster.objects.update_or_create(
                            post_type=str(post_type_val).strip().lower(),
                            role_name=str(role_name_val).strip(),
                            defaults={'description': str(desc_val).strip()}
                        )
                        imported += 1
                    else:
                        skipped += 1
                        
                self.message_user(request, f"✓ Successfully imported {imported} posts. (Skipped {skipped} incomplete rows)")
                return redirect(request.path)
            except Exception as e:
                self.message_user(request, f"✗ Error importing: {str(e)}", level='error')
                
        return render(request, 'admin/import_form.html', {
            'title': 'Import Posts',
            'module': 'Posts',
            'sample_csv': reverse('admin:post_export_sample')
        })
    
    def export_sample(self, request):
        file_format = request.GET.get('format', 'csv')
        filename = f'posts_sample.{file_format}'
        
        if file_format == 'xlsx':
            return ImportExportHelper.generate_xlsx_response(
                filename, 'Posts',
                POSTS_SAMPLE['headers'],
                POSTS_SAMPLE['rows']
            )
        else:
            return ImportExportHelper.generate_csv_response(
                filename,
                POSTS_SAMPLE['headers'],
                POSTS_SAMPLE['rows']
            )


@admin.register(EmailMapping)
class EmailMappingAdmin(ExportMixin, admin.ModelAdmin):
    list_display = ("entity_link", "post", "email_link", "person_name", 'phone_number', "is_primary_badge", "is_active")
    list_filter = ("mapping_type", "is_primary", "is_active", "post", "created_at")
    search_fields = ("email", "person_name", "center__name", "department__name", "post__role_name")
    readonly_fields = ("created_at", "updated_at")
    change_list_template = "admin/approval_core/emailmapping/change_list.html"

    fieldsets = (
        ("Email Mapping", {
            "fields": ("post", "mapping_type", "person_name", 'phone_number', "email")
        }),
        ("Center/Department Assignment", {
            "fields": ("center", "department"),
            "description": "Select either center or department"
        }),
        ("Priority", {
            "fields": ("is_primary",),
            "description": "Mark as primary email for this role"
        }),
        ("Status", {
            "fields": ("is_active",)
        }),
        ("Timestamps", {
            "fields": ("created_at", "updated_at"),
            "classes": ("collapse",)
        }),
    )

    def entity_link(self, obj):
        if obj.center:
            return f"C: {obj.center.name[:20]}"
        return f"D: {obj.department.name[:20]}"
    entity_link.short_description = "Entity"

    def email_link(self, obj):
        return format_html(f'<a href="mailto:{obj.email}" style="color: #007bff;">{obj.email}</a>')
    email_link.short_description = "Email"

    def is_primary_badge(self, obj):
        if obj.is_primary:
            return format_html('<span style="background-color: #dc3545; color: white; padding: 3px 6px; border-radius: 3px;">Primary</span>')
        return format_html('<span style="background-color: #6c757d; color: white; padding: 3px 6px; border-radius: 3px;">Secondary</span>')
    is_primary_badge.short_description = "Priority"
    
    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path('import/', self.admin_site.admin_view(self.import_email_mappings), name='emailmapping_import'),
            path('export-sample/', self.admin_site.admin_view(self.export_sample), name='emailmapping_export_sample'),
        ]
        return custom_urls + urls

    def import_email_mappings(self, request):
        if request.method == 'POST' and request.FILES.get('file'):
            file = request.FILES['file']
            try:
                if file.name.endswith('.csv'):
                    data = ImportExportHelper.parse_csv_file(file)
                elif file.name.endswith('.xlsx'):
                    data = ImportExportHelper.parse_xlsx_file(file)
                else:
                    self.message_user(request, "Please upload CSV or XLSX file", level='error')
                    return render(request, 'admin/import_form.html', {'title': 'Import Email Mappings', 'module': 'Email Mappings'})
                
                imported = 0
                skipped = 0
                for row in data:
                    try:
                        # 🟢 1. Flexible key capture for all possible header formats
                        post_identifier = row.get('post') or row.get('post_id') or row.get('Post')
                        post_type_fallback = row.get('post_type') or row.get('Post Type')
                        role_name_fallback = row.get('role_name') or row.get('Role Name')
                        
                        mapping_type = row.get('mapping_type') or row.get('Mapping Type') or 'center'
                        email_val = row.get('email') or row.get('Email')
                        
                        center_identifier = row.get('center_code') or row.get('center') or row.get('Center')
                        dept_identifier = row.get('department_code') or row.get('department') or row.get('Department')

                        if not email_val:
                            skipped += 1
                            continue

                        # 🟢 2. Smart Postmaster Lookup (Handles ID, explicit string identifier, or fallback columns)
                        post = None
                        if post_identifier:
                            if str(post_identifier).strip().isdigit():
                                post = PostMaster.objects.get(id=int(post_identifier))
                            else:
                                post = PostMaster.objects.get(role_name=str(post_identifier).strip())
                        elif post_type_fallback and role_name_fallback:
                            post = PostMaster.objects.get(post_type=str(post_type_fallback).strip().lower(), role_name=str(role_name_fallback).strip())
                        
                        if not post:
                            skipped += 1
                            continue

                        # 🟢 3. Smart Center Lookup (Handles numeric ID or alphanumeric code text)
                        center = None
                        if center_identifier and str(center_identifier).strip() not in ['', 'None', 'NaN', '-']:
                            if str(center_identifier).strip().isdigit():
                                center = Center.objects.get(id=int(center_identifier))
                            else:
                                center = Center.objects.get(code=str(center_identifier).strip())

                        # 🟢 4. Smart Department Lookup (Handles numeric ID or alphanumeric code text)
                        department = None
                        if dept_identifier and str(dept_identifier).strip() not in ['', 'None', 'NaN', '-']:
                            if str(dept_identifier).strip().isdigit():
                                department = Department.objects.get(id=int(dept_identifier))
                            else:
                                department = Department.objects.get(code=str(dept_identifier).strip())

                        # 🟢 5. Parse clean priority state metrics cleanly
                        is_primary_str = str(row.get('is_primary') or row.get('Is Primary') or 'no').lower()
                        is_primary_bool = is_primary_str in ['1', 'true', 'yes', 'primary']

                        is_active_str = str(row.get('is_active') or row.get('Is Active') or 'yes').lower()
                        is_active_bool = is_active_str in ['1', 'true', 'yes', '']

                        # 🟢 6. Update or create row entry using the email field as the lookup target
                        EmailMapping.objects.update_or_create(
                            post=post,
                            email=str(email_val).strip().lower(),
                            mapping_type=str(mapping_type).strip().lower(),
                            center=center,
                            department=department,
                            defaults={
                                'person_name': str(row.get('person_name', '') or row.get('Person Name', '')).strip(),
                                'phone_number': str(row.get('phone_number', '') or row.get('Phone Number', '')).strip(),
                                'is_primary': is_primary_bool,
                                'is_active': is_active_bool
                            }
                        )
                        imported += 1

                    except (PostMaster.DoesNotExist, Center.DoesNotExist, Department.DoesNotExist) as relation_error:
                        skipped += 1
                        continue

                self.message_user(request, f"✓ Successfully imported {imported} email mappings. (Skipped {skipped} rows due to missing connections)")
                return redirect(request.path)
            except Exception as e:
                self.message_user(request, f"✗ Error importing: {str(e)}", level='error')
                
        return render(request, 'admin/import_form.html', {
            'title': 'Import Email Mappings',
            'module': 'Email Mappings',
            'sample_csv': reverse('admin:emailmapping_export_sample')
        })
    
    def export_sample(self, request):
        file_format = request.GET.get('format', 'csv')
        filename = f'email_mappings_sample.{file_format}'
        
        if file_format == 'xlsx':
            return ImportExportHelper.generate_xlsx_response(
                filename, 'Email Mappings',
                EMAIL_MAPPINGS_SAMPLE['headers'],
                EMAIL_MAPPINGS_SAMPLE['rows']
            )
        else:
            return ImportExportHelper.generate_csv_response(
                filename,
                EMAIL_MAPPINGS_SAMPLE['headers'],
                EMAIL_MAPPINGS_SAMPLE['rows']
            )

# ==================== SECTION: EXCHANGE RATES ====================

@admin.register(ExchangeRate)
class ExchangeRateAdmin(admin.ModelAdmin):
    list_display = (
        'currency_code', 'currency_symbol_display',
        'rate_to_inr', 'fetched_date', 'fetched_at'
    )
    readonly_fields = ('fetched_date', 'fetched_at')
    search_fields = ('currency_code',)
    ordering = ('currency_code',)

    fieldsets = (
        ("Currency Info", {
            "fields": ("currency_code", "currency_symbol"),
            "description": "Currency code (e.g. USD) and symbol (e.g. $). "
                           "These are auto-populated when a form is submitted by a foreign user."
        }),
        ("Rate", {
            "fields": ("rate_to_inr",),
            "description": "1 unit of this currency = X INR. "
                           "This is fetched automatically from frankfurter.app daily."
        }),
        ("Fetch Info", {
            "fields": ("fetched_date", "fetched_at"),
            "classes": ("collapse",)
        }),
    )

    def currency_symbol_display(self, obj):
        return format_html(
            '<span style="font-size:1.2rem; font-weight:bold;">{}</span>',
            obj.currency_symbol or '—'
        )
    currency_symbol_display.short_description = "Symbol"

    def has_add_permission(self, request):
        return False  # Rates are auto-fetched, not manually added
