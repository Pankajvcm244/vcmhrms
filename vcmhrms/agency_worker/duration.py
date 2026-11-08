# Copyright (c) 2026, Pankaj Sharma and contributors
# For license information, please see license.txt

def hours_to_hhmm(value):
    """Format internally stored decimal hours as an unambiguous HH:MM duration."""
    try:
        minutes=round(float(value or 0)*60)
    except (TypeError,ValueError):
        minutes=0
    sign='-' if minutes<0 else ''
    minutes=abs(minutes)
    hours,remainder=divmod(minutes,60)
    return f'{sign}{hours:02d}:{remainder:02d}'
