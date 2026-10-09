"""Pure attendance calculation; original directionless punches are never modified."""
from datetime import timedelta


def evaluate_times(result,duty,first,last,complete=True):
    """Apply working-hour and punctuality rules to an inferred or manager-corrected pair."""
    if not first or not last or last<=first:
        result.update(status='Needs Review',exception='A valid first and last punch pair is required.')
        return result
    hours=max(0,(last-first).total_seconds()/3600-duty['break_minutes']/60)
    required=float(duty['required_hours'])
    before_ot=max(0,(duty['start']-first).total_seconds()/3600)
    after_ot=max(0,(last-duty['end']).total_seconds()/3600)
    result.update(first_in=first,last_out=last,worked_hours=round(hours,2),required_hours=required,
        shortfall_hours=round(max(0,required-hours),2),
        late_entry=int(first>duty['start']+timedelta(minutes=duty.get('late_entry_grace_minutes',15))),
        early_exit=int(last<duty['end']-timedelta(minutes=duty.get('early_exit_grace_minutes',15))),
        actual_before_ot_hours=round(before_ot,2),actual_after_ot_hours=round(after_ot,2))
    if complete:
        result['status']='Present' if hours>=required else 'Half Day' if hours>=required/2 else 'Absent'
    return result


def calculate(duties, punches, now, synced_through=None):
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
    for index, duty in enumerate(duties):
        low, high = windows[index]
        matched = sorted(set(p for p in punches if low <= p <= high))
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
            result.update(first_in=matched[0], status='Needs Review', exception='Only one punch; missing counterpart.')
        elif len(matched) >= 2:
            first, last = matched[0], matched[-1]
            evaluate_times(result,duty,first,last,complete)
        elif complete:
            result['status'] = 'Absent'
        results.append(result)
    return results
