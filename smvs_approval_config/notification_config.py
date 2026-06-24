"""
SMS and WhatsApp notification configuration
Cleaned version routing completely via TextGuru and Pinbot.ai gateways.
Keys are perfectly matched to stored database model choice values.
"""
from decouple import config

# Notification Settings Flags
ENABLE_SMS_NOTIFICATIONS = config('ENABLE_SMS_NOTIFICATIONS', default=True, cast=bool)
ENABLE_WHATSAPP_NOTIFICATIONS = config('ENABLE_WHATSAPP_NOTIFICATIONS', default=True, cast=bool)
SMS_MAX_LENGTH = 160  # Standard SMS length

# Approval Level Notification Channel Mapping Rules
# 🟢 FIXED: Keys match your stored database UserRole choices exactly!
APPROVAL_LEVEL_NOTIFICATION_CONFIG = {
    "operator": {
        "channels": ["email", "sms"],
        "template": "operator_approval",
    },
    "mk_sabhya": {  # 👈 Matches choice key ("mk_sabhya", "MK Sabhya")
        "channels": ["email", "sms", "whatsapp"],
        "template": "mk_sabhya_approval",
    },
    "mk_sant": {    # 👈 Matches choice key ("mk_sant", "MK Sant 1")
        "channels": ["email", "sms", "whatsapp"],
        "template": "mk_sant_approval",
    },
    "p_rajipaswami": { # 👈 Matches choice key ("p_rajipaswami", "MK Sant 2")
        "channels": ["email", "sms", "whatsapp"],
        "template": "p_rajipaswami_approval",
    },
    "hdh_guruji": {   # 👈 Matches choice key ("hdh_guruji", "HDH Guruji")
        "channels": ["email", "sms", "whatsapp"],
        "template": "hdh_guruji_approval",
    },
    "third_party": {
        "channels": ["email", "sms"],
        "template": "third_party_verification",
    },
}