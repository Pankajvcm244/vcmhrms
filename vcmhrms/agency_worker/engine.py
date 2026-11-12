# Copyright (c) 2026, Pankaj Sharma and contributors
# For license information, please see license.txt

"""Pure attendance calculation; original directionless punches are never modified."""
from datetime import timedelta


def evaluate_times(result,duty,first,last,complete=True):
    """Apply working-hour and punctuality rules to an inferred or manager-corrected pair."""
    if not first or not last or last<=first:
        result.update(status='Needs Review',exception='A valid first and last punch pair is required.')
        return result
    hours=max(0,(last-first).total_seconds()/3600)
    required=float(duty['required_hours'])
    half_day=float(duty.get('half_day_hours') or required/2)
    raw_before_ot=max(0,(duty['start']-first).total_seconds()/3600)
    raw_after_ot=max(0,(last-duty['end']).total_seconds()/3600)
    # OT is based on net duration beyond Required Hours, not merely on how late
    # the last punch is compared with scheduled end. This prevents a late arrival
    # from being treated as OT. Less than one complete extra hour is not eligible.
    eligible_ot=max(0,hours-required)
    if eligible_ot < 1:
        before_ot=after_ot=0
    else:
        before_ot=min(raw_before_ot,eligible_ot)
        after_ot=min(raw_after_ot,max(0,eligible_ot-before_ot))
        # Required Hours may differ from scheduled span (for example because of
        # breaks). Keep the displayed before/after total equal to eligible OT.
        after_ot+=max(0,eligible_ot-before_ot-after_ot)
    result.update(first_in=first,last_out=last,worked_hours=round(hours,2),required_hours=required,
        shortfall_hours=round(max(0,required-hours),2),
        late_entry=0,early_exit=0,
        actual_before_ot_hours=round(before_ot,2),actual_after_ot_hours=round(after_ot,2))
    if complete:
        result['status']='Present' if hours>=required else 'Half Day' if hours>=half_day else 'Absent'
    return result


def calculate(duties, punches, now):
    results = []
    windows = [(d['start'] - timedelta(hours=max(d.get('planned_before_ot_hours', 0),
                                                   d.get('max_before_ot_capture_hours', 0)), minutes=d['early']),
                d['end'] + timedelta(hours=max(d.get('planned_after_ot_hours', 0),
                                               d.get('max_after_ot_capture_hours', 0)), minutes=d['late'])) for d in duties]
    # Broad OT capture windows must not swallow punches belonging to another rostered duty.
    # Split overlapping capture windows at the midpoint between adjacent scheduled duties.
    ordered=sorted(range(len(duties)),key=lambda index:duties[index]['start'])
    for left_index,right_index in zip(ordered,ordered[1:]):
        left_low,left_high=windows[left_index]
        right_low,right_high=windows[right_index]
        if left_high>right_low:
            boundary=duties[left_index]['end']+(duties[right_index]['start']-duties[left_index]['end'])/2
            windows[left_index]=(left_low,min(left_high,boundary))
            windows[right_index]=(max(right_low,boundary),right_high)
    # Normal capture limits are deliberately conservative. If they find only one
    # punch, allow recovery of its counterpart from the otherwise unowned gap up
    # to the midpoint before the adjacent rostered duty. These exclusive
    # territories cannot steal a punch from another duty and support workers who
    # complete Required Hours later than the scheduled end.
    territories=list(windows)
    if ordered:
        first_index,last_index=ordered[0],ordered[-1]
        first_low,first_high=territories[first_index]
        last_low,last_high=territories[last_index]
        territories[first_index]=(min(first_low,duties[first_index]['start']-timedelta(hours=12)),first_high)
        territories[last_index]=(last_low,max(last_high,duties[last_index]['end']+timedelta(hours=12)))
    for left_index,right_index in zip(ordered,ordered[1:]):
        left_low,left_high=territories[left_index]
        right_low,right_high=territories[right_index]
        if left_high < right_low:
            boundary=duties[left_index]['end']+(duties[right_index]['start']-duties[left_index]['end'])/2
            territories[left_index]=(left_low,max(left_high,boundary))
            territories[right_index]=(min(right_low,boundary),right_high)
    for index, duty in enumerate(duties):
        low, high = windows[index]
        matched = sorted(set(p for p in punches if low <= p <= high))
        # Conservative fallback for day duties only. Extending an overnight duty
        # can pair today's missing OUT with tomorrow's IN, so overnight anomalies
        # remain Needs Review unless both punches are inside the normal window.
        if len(matched)==1 and duty['start'].date()==duty['end'].date():
            territory_low,territory_high=territories[index]
            recovered=sorted(set(p for p in punches
                if territory_low <= p <= territory_high and p.date()==duty['start'].date()))
            if len(recovered)>=2:
                matched=recovered
        ambiguous = any(sum(a <= p <= b for a, b in windows) > 1 for p in matched)
        # Finalize by the configured capture-window closing time. A delayed historical
        # punch is still accepted later and causes recalculation of the affected result.
        complete = now >= high
        result = dict(roster_duty=duty['id'], shift=duty['shift'], first_in=None,
                      last_out=None, worked_hours=0, actual_before_ot_hours=0,
                      actual_after_ot_hours=0,
                      required_hours=duty['required_hours'],shortfall_hours=duty['required_hours'],
                      late_entry=0,early_exit=0,
                      planned_before_ot_hours=duty.get('planned_before_ot_hours',0),
                      planned_after_ot_hours=duty.get('planned_after_ot_hours',0),
                      ot_reason=duty.get('ot_reason'),status='Pending',exception='')
        if ambiguous:
            result.update(status='Needs Review', exception='Punch matches more than one duty window.')
        elif len(matched) == 1:
            result['first_in']=matched[0]
            if complete:
                result.update(status='Needs Review', exception='Only one punch; missing counterpart.')
        elif len(matched) >= 2:
            first, last = matched[0], matched[-1]
            evaluate_times(result,duty,first,last,complete)
        elif complete:
            result['status'] = 'Absent'
        results.append(result)
    return results



