#!/usr/bin/env python
import os
import sys
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'smvs_approval_config.settings')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

django.setup()

from django.contrib.auth.models import User
from approval_core.models import UserRole, Department, ApprovalLevel, ApprovalRule, RuleApprovalSequence

# Create superuser
if not User.objects.filter(username='admin').exists():
    admin = User.objects.create_superuser('admin', 'admin@smvs.com', 'admin123')
    print(f"Admin user created: {admin.username}")
else:
    admin = User.objects.get(username='admin')
    print(f"Admin user already exists: {admin.username}")

# Create UserRole for admin
if not UserRole.objects.filter(user=admin).exists():
    UserRole.objects.create(user=admin, role='admin')
    print("Admin role created")

# Create default departments
departments = [
    ('Head Office', 'HO'),
    ('Finance', 'FIN'),
    ('Operations', 'OPS'),
    ('HR', 'HR'),
]

for name, code in departments:
    if not Department.objects.filter(code=code).exists():
        Department.objects.create(name=name, code=code)
        print(f"Department created: {name}")

# Create default approval levels
levels = [
    (1, 'Operator'),
    (2, 'MK Sabhya'),
    (3, 'MK Sant'),
    (4, 'P.Rajipaswami'),
    (5, 'HDH Guruji'),
    (6, '3rd Party Verification'),
]

for level_num, level_name in levels:
    if not ApprovalLevel.objects.filter(level_number=level_num).exists():
        ApprovalLevel.objects.create(level_number=level_num, level_name=level_name)
        print(f"Approval level created: {level_name}")

# Create default approval rules
if not ApprovalRule.objects.filter(rule_name='Amount < 50000').exists():
    rule1 = ApprovalRule.objects.create(
        rule_name='Amount < 50000',
        rule_type='amount',
        max_amount=50000,
        priority=1
    )
    # Add approval levels for this rule
    for seq, level in enumerate(ApprovalLevel.objects.filter(level_number__lte=3).order_by('level_number'), 1):
        RuleApprovalSequence.objects.create(rule=rule1, approval_level=level, sequence_order=seq)
    print("Rule created: Amount < 50000")

if not ApprovalRule.objects.filter(rule_name='Amount 50000 - 200000').exists():
    rule2 = ApprovalRule.objects.create(
        rule_name='Amount 50000 - 200000',
        rule_type='amount',
        min_amount=50000,
        max_amount=200000,
        priority=2
    )
    for seq, level in enumerate(ApprovalLevel.objects.filter(level_number__lte=4).order_by('level_number'), 1):
        RuleApprovalSequence.objects.create(rule=rule2, approval_level=level, sequence_order=seq)
    print("Rule created: Amount 50000 - 200000")

if not ApprovalRule.objects.filter(rule_name='Amount > 200000').exists():
    rule3 = ApprovalRule.objects.create(
        rule_name='Amount > 200000',
        rule_type='amount',
        min_amount=200000,
        priority=3
    )
    for seq, level in enumerate(ApprovalLevel.objects.filter(level_number__lte=5).order_by('level_number'), 1):
        RuleApprovalSequence.objects.create(rule=rule3, approval_level=level, sequence_order=seq)
    print("Rule created: Amount > 200000")

print("\nSetup complete!")
print("\nAdmin credentials:")
print("Username: admin")
print("Password: admin123")
print("\nAccess the admin panel at: http://localhost:8000/admin/")
