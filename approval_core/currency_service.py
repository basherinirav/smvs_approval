# approval_core/currency_service.py
import requests
import logging
from decimal import Decimal, ROUND_HALF_UP
from django.utils import timezone

logger = logging.getLogger(__name__)

# Free API — no key needed, reliable, 1500 req/day
EXCHANGE_API_URL = "https://api.frankfurter.app/latest?to=INR&from={}"


def get_exchange_rate(currency_code: str) -> Decimal:
    """
    Returns rate: 1 unit of currency_code = X INR.

    Strategy:
    1. If INR → return 1 immediately
    2. Check DB for today's cached rate → use if found
    3. Fetch from live API → cache in DB → return
    4. If API fails → use yesterday's stale rate from DB
    5. If nothing available → return 1 (safe fallback, logs error)
    """
    from approval_core.models import ExchangeRate

    if not currency_code or currency_code.upper() == 'INR':
        return Decimal('1.000000')

    currency_code = currency_code.upper()
    today = timezone.now().date()

    # ── Step 1: Try today's cached rate ──────────────────────────
    try:
        rate_obj = ExchangeRate.objects.get(currency_code=currency_code)
        if rate_obj.fetched_date == today:
            logger.debug(f"[ExchangeRate] Cache hit: 1 {currency_code} = ₹{rate_obj.rate_to_inr}")
            return rate_obj.rate_to_inr
    except ExchangeRate.DoesNotExist:
        rate_obj = None

    # ── Step 2: Fetch live from API ───────────────────────────────
    try:
        url = EXCHANGE_API_URL.format(currency_code)
        response = requests.get(url, timeout=5)
        response.raise_for_status()
        data = response.json()

        inr_rate = Decimal(str(data['rates']['INR'])).quantize(
            Decimal('0.000001'), rounding=ROUND_HALF_UP
        )

        # Save or update in DB
        ExchangeRate.objects.update_or_create(
            currency_code=currency_code,
            defaults={
                'rate_to_inr': inr_rate,
                'fetched_date': today,
            }
        )
        logger.info(f"[ExchangeRate] Live fetch: 1 {currency_code} = ₹{inr_rate}")
        return inr_rate

    except Exception as e:
        logger.warning(f"[ExchangeRate] API failed for {currency_code}: {e}")

    # ── Step 3: Fallback to stale DB rate ─────────────────────────
    if rate_obj:
        logger.warning(
            f"[ExchangeRate] Using stale rate from {rate_obj.fetched_date}: "
            f"1 {currency_code} = ₹{rate_obj.rate_to_inr}"
        )
        return rate_obj.rate_to_inr

    # ── Step 4: Hard fallback ─────────────────────────────────────
    logger.error(
        f"[ExchangeRate] No rate available for {currency_code}. "
        f"Defaulting to 1 (treating as INR equivalent)."
    )
    return Decimal('1.000000')


def convert_to_inr(amount: Decimal, currency_code: str) -> tuple:
    """
    Convert a local currency amount to INR.

    Returns:
        (amount_inr: Decimal, rate_used: Decimal)

    Example:
        convert_to_inr(Decimal('1000'), 'USD') → (Decimal('83500.00'), Decimal('83.500000'))
    """
    rate = get_exchange_rate(currency_code)
    amount_inr = (amount * rate).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    return amount_inr, rate


def convert_from_inr(amount_inr: Decimal, rate: Decimal) -> Decimal:
    """
    Convert an INR amount back to local currency using the stored rate.
    Uses the SAME rate that was stored at submission — no re-fetching.

    Args:
        amount_inr: Amount in INR (e.g. approved_amount)
        rate: The exchange_rate_used stored on the form

    Returns:
        Local currency equivalent (Decimal)

    Example:
        convert_from_inr(Decimal('83500'), Decimal('83.5')) → Decimal('1000.00')
    """
    if not rate or rate == 0:
        return amount_inr
    result = (amount_inr / rate).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    return result


def get_user_currency(user) -> dict:
    """
    Detects the user's currency based on their country.

    Chain:
        user → UserRole → department → country → currency_code/symbol
        user → UserRole → center → zone → country → currency_code/symbol

    Returns:
        dict with keys: 'code' (str), 'symbol' (str), 'is_foreign' (bool)

    Example:
        {'code': 'USD', 'symbol': '$', 'is_foreign': True}
        {'code': 'INR', 'symbol': '₹', 'is_foreign': False}
    """
    DEFAULT = {'code': 'INR', 'symbol': '₹', 'is_foreign': False}

    try:
        user_role = user.approval_role  # OneToOne via UserRole.user
        country = None

        # Priority 1: Department → Country
        if user_role.department and user_role.department.country:
            country = user_role.department.country

        # Priority 2: Center → Zone → Country
        elif (
            user_role.center and
            user_role.center.zone and
            user_role.center.zone.country
        ):
            country = user_role.center.zone.country

        if country and country.currency_code:
            code = country.currency_code.upper()
            symbol = country.currency_symbol or '₹'
            return {
                'code': code,
                'symbol': symbol,
                'is_foreign': code != 'INR',
            }

    except Exception as e:
        logger.debug(f"[get_user_currency] Could not detect currency for {user}: {e}")

    return DEFAULT