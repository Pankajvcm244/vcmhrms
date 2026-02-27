import frappe
from frappe.utils import get_datetime, getdate, add_days, flt, cint
from datetime import datetime, timedelta, time as pytime
import logging
logger = logging.getLogger(__name__)

def _to_time(t):
    """Shift start/end sometimes come as time / timedelta / string. Normalize to datetime.time."""
    if t is None:
        return None
    if isinstance(t, pytime):
        return t
    if isinstance(t, timedelta):
        total = int(t.total_seconds()) % 86400
        return pytime(hour=total // 3600, minute=(total % 3600) // 60, second=total % 60)
    parts = str(t).split(":")
    try:
        h, m = int(parts[0]), int(parts[1])
        s = int(parts[2]) if len(parts) > 2 else 0
        return pytime(hour=h, minute=m, second=s)
    except Exception:
        return None

def _shift_bounds_with_grace(shift, base_date):
    """
    Returns (win_start, win_end) for a shift on base_date, including grace minutes.

    - Uses Shift Type start_time / end_time (supports time, timedelta, string).
    - Handles overnight shifts (end <= start => end + 1 day).
    - Applies grace window:
        win_start = start_dt - begin_check_in_before... minutes
        win_end   = end_dt   + allow_check_out_after... minutes

    NOTE:
    For overnight shifts, the caller should try both base_date and base_date-1
    and pick the window that actually contains the punch datetime.
    """
    st = _to_time(getattr(shift, "start_time", None))
    et = _to_time(getattr(shift, "end_time", None))
    if not st or not et:
        return None, None
    d = getdate(base_date)
    start_dt = datetime.combine(d, st)
    end_dt = datetime.combine(d, et)
    if end_dt <= start_dt:
        end_dt = end_dt + timedelta(days=1)  # overnight shift
    pre = int(float(getattr(shift, "begin_check_in_before_shift_start_time", 0) or 0))
    post = int(float(getattr(shift, "allow_check_out_after_shift_end_time", 0) or 0))
    return (start_dt - timedelta(minutes=pre), end_dt + timedelta(minutes=post))

def apply_valid_window_in_out(doc, method=None):
    """
    Enforce "Valid IN/OUT" semantics inside the shift grace window.
    What this does:
    - Marks punches outside the allowed window as offshift=1 so HRMS ignores them.
    - We skip this logic for flexible shifts (custom_flexible_shift=1), where you want pure duration-based hours.
    - For punches inside the window, deterministically sets:
        For non-flexible shifts:
            - Only FIRST punch in window is IN, skip_auto_attendance=0, 
            - Only LAST  punch in window is OUT, skip_auto_attendance=0, 
            - All middle punches are ignored log_type="", skip_auto_attendance=1, 
            This prevents HRMS “IN/OUT pairing” from producing short working_hours
    when users add an extra punch in between.
      (HRMS then calculates working hours based on valid IN/OUT)
    Critical fixes included:
    - Overnight shifts: window might belong to previous attendance date.
    - Manual/test inserts: include the current doc in candidate set even if it's not in DB yet.
    - Deterministic: every validate recomputes first/last across the full candidate list.
    """
    # Prevent recursion if any downstream code triggers validate again
    if getattr(frappe.flags, "vcm_inout_guard", False):
        return
    # Must have employee + time + shift
    if not doc.employee or not doc.time or not doc.shift:
        return
    try:
        frappe.flags.vcm_inout_guard = True
        shift = frappe.get_cached_doc("Shift Type", doc.shift)
        # Optional: if you really want ONE flexible shift where pure duration counts,
        # don't force IN/OUT. Let HRMS handle it.
        if int(getattr(shift, "custom_flexible_shift", 0) or 0):
            logger.debug(f"[INOUT] Shift {shift.name} is flexible; skipping valid window logic")
            return
        
        # ----------------------------
        # 1)  Resolve correct grace window for this punch  (overnight-safe)
        #    Try both punch_date and punch_date-1; pick the one containing punch_dt.
        # ----------------------------
        punch_dt = get_datetime(doc.time)
        if not punch_dt:
            return
        # ------------------------------------------------------------
        # 1) Resolve correct grace window for this punch (overnight-safe)
        # ------------------------------------------------------------
        d0 = getdate(punch_dt)
        w0_start, w0_end = _shift_bounds_with_grace(shift, d0)
        d1 = add_days(d0, -1)
        w1_start, w1_end = _shift_bounds_with_grace(shift, d1)
        w_start, w_end = w0_start, w0_end
        if w1_start and w1_end and w1_start <= punch_dt <= w1_end:
            w_start, w_end = w1_start, w1_end
        if not w_start or not w_end:
            return
        # logger.debug(f"[INOUT] Validating checkin {doc.name} time={punch_dt} ")
        # Outside grace window => ignored by HRMS
        if punch_dt < w_start or punch_dt > w_end:
            doc.offshift = 1
            return
        # Inside window => always on-shift, but log_type (IN/OUT) to be determined by position in window
        doc.offshift = 0 
        # If this is an unsaved doc without a name yet, we cannot deterministically
        # reclassify the entire window (because we cannot include it safely).
        # Next validate after insert will reclassify correctly.
        if not doc.name:
            return
        
        # ------------------------------------------------------------
        # 2) Fetch other in-window punches (DB) and include current doc
        # ------------------------------------------------------------
        # IMPORTANT: do not rely on DB alone because current doc may not be committed yet.
        filters = {
            "employee": doc.employee,
            "shift": doc.shift,
            # "offshift": 0,                 # only in-window ones
            # don't filter skip_auto_attendance here; we want to reclassify everything deterministically
            "time": ("between", [w_start, w_end]),
        }
        # Exclude current name if it exists (avoid duplicates)
        if doc.name:
            filters["name"] = ("!=", doc.name)
        db_logs = frappe.get_all(
            "Employee Checkin",
            filters=filters,
            fields=["name", "time", "skip_auto_attendance", "log_type"],
            order_by="time asc",
            limit_page_length=2000,
        )
        # If nothing in DB yet, let the next validate classify.
        if not db_logs:
            return
        
        # Build candidate list INCLUDING current doc even if not in DB yet
        candidates = list(db_logs)
        # If current doc isn't present in db_logs (typical during insert validate), add it
        if doc.name and all(r["name"] != doc.name for r in candidates):
            candidates.append({"name": doc.name, "time": punch_dt})
        # Sort by time to find first/last deterministically
        candidates.sort(key=lambda r: get_datetime(r["time"]))
        if not candidates:
            return
        first_name = candidates[0]["name"]
        last_name = candidates[-1]["name"]
        # logger.debug(
        #     f"[INOUT] Candidates count={len(candidates)} first={first_name}({candidates[0]['time']}) "
        #     f"last={last_name}({candidates[-1]['time']})"
        # )
        # Helper: update DB rows only (avoid db.set_value on doc that isn't inserted yet)
        db_names = {r["name"] for r in db_logs}
        def _set_db(name: str, values: dict):
            if name in db_names:
                frappe.db.set_value("Employee Checkin", name, values, update_modified=False)
        # Re-classify all candidates every time (prevents "two OUTs" leftovers)
        for r in candidates:
            name = r["name"]
            if name == first_name and name == last_name:
                # Only one punch in window (single-entry day): mark as IN, keep it usable
                if name == doc.name:
                    doc.log_type = "IN"
                    doc.skip_auto_attendance = 0
                    doc.offshift = 0
                _set_db(name, {"log_type": "IN", "skip_auto_attendance": 0, "offshift": 0})
                continue
            if name == first_name:
                if name == doc.name:
                    doc.log_type = "IN"
                    doc.skip_auto_attendance = 0
                    doc.offshift = 0
                _set_db(name, {"log_type": "IN", "skip_auto_attendance": 0, "offshift": 0})
            elif name == last_name:
                if name == doc.name:
                    doc.log_type = "OUT"
                    doc.skip_auto_attendance = 0
                    doc.offshift = 0
                _set_db(name, {"log_type": "OUT", "skip_auto_attendance": 0, "offshift": 0})
            else:
                # Middle punch: ignore for auto-attendance but keep offshift clean
                if name == doc.name:
                    doc.log_type = ""
                    doc.skip_auto_attendance = 1
                    doc.offshift = 0
                _set_db(name, {"log_type": "", "skip_auto_attendance": 1, "offshift": 0})
        # logger.debug(
        #     f"[INOUT] Final for {doc.name}: log_type={getattr(doc,'log_type',None)} "
        #     f"skip={getattr(doc,'skip_auto_attendance',None)} offshift={getattr(doc,'offshift',None)}"
        # )
    finally:
        frappe.flags.vcm_inout_guard = False


def apply_nonflex_strict_hours(doc, method=None):
    """
    NON-flex shift enforcement (post-HRMS, but before submit):
    - HRMS calculates hours from valid IN/OUT punch times (which may include grace OUT).
    - For non-flex shifts (custom_flexible_shift=0), we clamp WORKING HOURS to strict shift bounds
      (NO grace), so grace punches don't inflate working hours.
    - Then we re-evaluate:
        * late_entry / early_exit
        * status (Absent / Half Day / Present) using Shift thresholds
      so Attendance is consistent.

    Safe guards:
    - Skip cancelled
    - Skip leave-driven statuses (On Leave / Holiday) so we don't fight Leave logic
    - Skip flexible shifts (custom_flexible_shift=1)
    Must play nicely with enforce_late_entry_policy():
      - We may mark Half Day due to strict hours
      - Later policy can upgrade to Present for first N late entries
      - Never override Absent
      - Early exit stays Half Day (policy hook will enforce too)
    """
    # Ignore cancelled
    if getattr(doc, "docstatus", 0) == 2:
        return
    # Must have shift + in/out to do anything
    if not getattr(doc, "shift", None) or not getattr(doc, "attendance_date", None):
        return
    if not getattr(doc, "in_time", None) or not getattr(doc, "out_time", None):
        return
    # Skip leave-driven attendance
    status_lc = (getattr(doc, "status", "") or "").strip().lower()
    if status_lc in ("on leave", "holiday"):
        return
    if getattr(doc, "leave_application", None) or getattr(doc, "leave_type", None):
        return
    shift = frappe.get_cached_doc("Shift Type", doc.shift)
    # Flexible shift => do nothing
    if cint(getattr(shift, "custom_flexible_shift", 0) or 0):
        return
    in_dt = get_datetime(doc.in_time)
    out_dt = get_datetime(doc.out_time)
    if not in_dt or not out_dt or out_dt <= in_dt:
        return
    # Compute STRICT bounds for this attendance_date (overnight-safe)
    strict_start, strict_end = _strict_shift_bounds_for_attendance(
        shift=shift,
        attendance_date=doc.attendance_date,
        in_dt=in_dt,
        out_dt=out_dt,
    )
    if not strict_start or not strict_end:
        return
    # Mark "late but within grace" (this is what your quota is based on)
    # Example: shift start 09:30, grace 15 => 09:30 < in_time <= 09:45
    if cint(getattr(shift, "enable_late_entry_marking", 0) or 0) and in_dt:
        late_grace = cint(getattr(shift, "late_entry_grace_period", 0) or 0)
        grace_cutoff = strict_start + timedelta(minutes=late_grace)

        within_grace = 1 if (in_dt > strict_start and in_dt <= grace_cutoff) else 0
        beyond_grace = 1 if (in_dt > grace_cutoff) else 0

        # store for later policy counting (add this custom checkbox field on Attendance)
        if hasattr(doc, "custom_late_entry_within_grace"):
            doc.custom_late_entry_within_grace = within_grace
            logger.debug(f"[ATT-CLAMP 9] Late entry within grace check for {doc.name}: in_dt={in_dt} strict_start={strict_start} late_grace={late_grace} grace_cutoff={grace_cutoff}" )   
        # Keep HRMS late_entry semantics intact (beyond grace)
        doc.late_entry = 1 if (in_dt > grace_cutoff) else 0
    # Clamp within strict bounds (NO grace)
    eff_in = max(in_dt, strict_start)
    eff_out = min(out_dt, strict_end)
    seconds = max(0, (eff_out - eff_in).total_seconds())
    wh = flt(seconds / 3600.0, 2)
    # Update working hours only (keep actual in/out times unchanged for audit)
    if flt(doc.working_hours or 0, 2) != wh:
        logger.debug(
            f"[ATT-CLAMP Updating WH] {doc.name} "
            f"actual_in={in_dt} actual_out={out_dt}  "
            f"eff_in={eff_in} eff_out={eff_out} wh={wh}"
        )
        doc.working_hours = wh 
    # Recompute late/early flags using STRICT bounds + HRMS grace-period fields
    # Late entry: in_time > shift_start + late_entry_grace_period
    if cint(getattr(shift, "enable_late_entry_marking", 0) or 0) and in_dt:
        late_grace = cint(getattr(shift, "late_entry_grace_period", 0) or 0)
        doc.late_entry = 1 if (in_dt > strict_start + timedelta(minutes=late_grace)) else 0
        logger.debug(
            f"[ATT-CLAMP] Late entry check for {doc.name}: in_dt={in_dt} strict_start={strict_start} "
            f"late_grace={late_grace} late_entry={doc.late_entry}"
        )
    else:
        doc.late_entry = 0
    # Early exit: out_time < shift_end - early_exit_grace_period
    if cint(getattr(shift, "enable_early_exit_marking", 0) or 0) and out_dt:
        early_grace = cint(getattr(shift, "early_exit_grace_period", 0) or 0)
        doc.early_exit = 1 if (out_dt < strict_end - timedelta(minutes=early_grace)) else 0
    # -----------------------------
    # Recompute STATUS based on clamped working hours
    # -----------------------------
    # HRMS logic in get_attendance():
    # if wh < absent_thr => Absent
    # if wh < half_day_thr => Half Day
    # else Present
    absent_thr = getattr(shift, "working_hours_threshold_for_absent", None)
    half_day_thr = getattr(shift, "working_hours_threshold_for_half_day", None)
    # Only if thresholds exist
    if absent_thr is None or half_day_thr is None:
        return    
    absent_thr = float(absent_thr)
    half_day_thr = float(half_day_thr)    
    if wh < absent_thr:
        new_status = "Absent"
    elif wh < half_day_thr:
        new_status = "Half Day"
    else:
        new_status = "Present"

    if (doc.status or "").strip() != new_status:
        logger.debug(f"[ATT-CLAMP] Status override {doc.name}: old={doc.status} new={new_status} wh={wh}")
        doc.status = new_status
        # Keep half_day_status consistent
        if new_status != "Half Day" and hasattr(doc, "half_day_status"):
            doc.half_day_status = None
        elif new_status == "Half Day" and hasattr(doc, "half_day_status") and not doc.half_day_status:
            # optional default; change if your org uses a different convention
            doc.half_day_status = "Absent"   

def _strict_shift_bounds_for_attendance(shift, attendance_date, in_dt, out_dt):
    """
    Compute strict shift bounds (NO grace) for this Attendance.
    Overnight-safe:
    - We try base_date = attendance_date and attendance_date - 1,
      and pick the strict window that best fits the in/out times.
    """
    st = _to_time(getattr(shift, "start_time", None))
    et = _to_time(getattr(shift, "end_time", None))
    if not st or not et:
        return None, None
    def strict_window(base_date):
        d = getdate(base_date)
        s = datetime.combine(d, st)
        e = datetime.combine(d, et)
        if e <= s:
            e = e + timedelta(days=1)
        return s, e
    d0 = getdate(attendance_date)
    s0, e0 = strict_window(d0)
    d1 = add_days(d0, -1)
    s1, e1 = strict_window(d1)
    # Choose the window that contains either in_dt or out_dt (prefer containment)
    if s0 <= in_dt <= e0 or s0 <= out_dt <= e0:
        return s0, e0
    if s1 <= in_dt <= e1 or s1 <= out_dt <= e1:
        return s1, e1
    # Fallback: choose the closer one by start distance
    if abs((in_dt - s1).total_seconds()) < abs((in_dt - s0).total_seconds()):
        return s1, e1
    return s0, e0

def _payroll_period_for_date(attendance_date):
    """
    Payroll/late-quota window derived from VCM HR Settings -> Attendance Locked Upto.
    Rule:
      - If locked_upto exists: current open period starts from locked_upto + 1 day
      - Period end is the given attendance_date (running quota up to that day)
      - If locked_upto is empty: fallback to last 31 days (first-time safety)
    """
    settings = frappe.get_cached_doc("VCM HR Settings")
    d = getdate(attendance_date)
    locked_upto = getdate(settings.attendance_locked_upto) if settings.attendance_locked_upto else None
    if locked_upto:
        return add_days(locked_upto, 1), d
    # First-time setup fallback
    return add_days(d, -31), d

def enforce_late_entry_policy(doc, method=None):
    """
    Policy (NON-flexible shifts only):
      1) early_exit == 1  => ALWAYS Half Day
       -If employee checks in after shift start but within (start + Late Entry Grace Period):
            allow only first N days in the current open payroll period (locked_upto+1..date)
            within quota → can remain Present even if strict-hours clamp made it Half Day
            over quota → force Half Day
                    (lateness is determined ONLY by HRMS late_entry flag, which itself uses
                     Shift Type: enable_late_entry_marking + late_entry_grace_period).
        - Never override Absent.
        - Skip leave-driven attendance.
    Assumptions:
      - HRMS already computed late_entry / early_exit on this Attendance
      - If you clamp in/out times, do that BEFORE this hook, but do not break HRMS late_entry semantics.
    """
    logger.debug(
        f"[LATE-POLICY] Evaluating {doc.name} status={doc.status} "
        f"late_entry={getattr(doc,'late_entry',None)} early_exit={getattr(doc,'early_exit',None)} "
        f"within_grace={getattr(doc,'custom_late_entry_within_grace',None)} "
        f"attendance_date={getattr(doc,'attendance_date',None)} in_time={getattr(doc,'in_time',None)} "
        f"working_hours={getattr(doc,'working_hours',None)}"
    )  
    # Ignore cancelled
    if getattr(doc, "docstatus", 0) == 2:
        logger.debug(f"[LATE-POLICY] Ignoring cancelled attendance {doc.name}")
        return
    # Must have required context
    if not getattr(doc, "employee", None) or not getattr(doc, "attendance_date", None) or not getattr(doc, "shift", None):
        logger.debug(f"[LATE-POLICY] Missing context for {doc.name}: employee={getattr(doc,'employee',None)} "
            f"attendance_date={getattr(doc,'attendance_date',None)} shift={getattr(doc,'shift',None)}")
        return
    # Skip leave-driven attendance (planned leave days shouldn't be penalized)
    status_lc = (doc.status or "").strip().lower()
    if status_lc == "on leave" or getattr(doc, "leave_application", None) or getattr(doc, "leave_type", None):
        logger.debug(f"[LATE-POLICY] Skipping leave-driven attendance {doc.name}: status={doc.status} "
            f"leave_application={getattr(doc,'leave_application',None)} leave_type={getattr(doc,'leave_type',None)}")   
        return
    shift = frappe.get_cached_doc("Shift Type", doc.shift)
    # Flexible shift => quota does not apply
    if cint(getattr(shift, "custom_flexible_shift", 0) or 0):
        logger.debug(f"[LATE-POLICY] Shift {shift.name} is flexible; skipping late entry policy")
        return
    # --------------------------
    # 0) Never override Absent
    # --------------------------
    if status_lc == "absent":
        logger.debug(f"[LATE-POLICY] Attendance {doc.name} is Absent; skipping policy") 
        return
    # 1) Early exit always Half Day
    if cint(getattr(doc, "early_exit", 0) or 0):
        if status_lc != "half day":
            logger.debug(f"[LATE-POLICY] Early exit => Half Day. att={doc.name} emp={doc.employee} date={doc.attendance_date}")
            doc.status = "Half Day"
            if hasattr(doc, "half_day_status") and not doc.half_day_status:
                doc.half_day_status = "Absent"
        return
    # 2) Late-within-grace quota applies ONLY if custom_late_within_grace=1
    if not cint(getattr(doc, "custom_late_entry_within_grace", 0) or 0):
        logger.debug(f"[LATE-POLICY] Late entry within grace NOT detected for {doc.name}; skipping quota checks")
        return
    # -----------------------------
    # HARD GUARD A: ensure in_time is REALLY within grace cutoff
    # (prevents weird cases like 5pm check-in from ever being treated as quota-eligible)
    # -----------------------------
    in_dt = get_datetime(getattr(doc, "in_time", None))
    if not in_dt:
        return
    strict_start, strict_end = _strict_shift_bounds_for_attendance(
        shift=shift,
        attendance_date=doc.attendance_date,
        in_dt=in_dt,
        out_dt=get_datetime(getattr(doc, "out_time", None)) or in_dt,
    )
    if not strict_start:
        return
    late_grace = cint(getattr(shift, "late_entry_grace_period", 0) or 0)
    grace_cutoff = strict_start + timedelta(minutes=late_grace)
    # Only eligible if (strict_start < in_time <= grace_cutoff)
    if not (in_dt > strict_start and in_dt <= grace_cutoff):
        logger.debug(
            f"[LATE-POLICY] Safety: in_dt outside grace window. "
            f"in_dt={in_dt} strict_start={strict_start} grace_cutoff={grace_cutoff} -> skip quota"
        )
        return
    # -----------------------------
    # HARD GUARD B: quota cannot upgrade if working_hours is below Half Day threshold
    # (i.e. if employee worked too little, keep Half Day/Absent)
    # -----------------------------
    half_day_thr = getattr(shift, "working_hours_threshold_for_absent", None)
    if half_day_thr is None:
        logger.debug(f"[LATE-POLICY] Missing half_day threshold on shift {shift.name}; skip quota upgrade.")
        return
    wh = flt(getattr(doc, "working_hours", 0) or 0, 2)
    half_day_thr = flt(float(half_day_thr), 2)
    if wh < half_day_thr:
        logger.debug(
            f"[LATE-POLICY] working_hours below working_hours_threshold_for_absent. wh={wh} < half_day_thr={half_day_thr} "
            f"-> do NOT upgrade to Present"
        )
        return
    # -----------------------------
    # Quota counting
    # -----------------------------
    settings = frappe.get_cached_doc("VCM HR Settings")
    allowed = cint(getattr(settings, "allowed_late_entry_count", 0) or 0)
    logger.debug(f"[LATE-POLICY] Late entry within grace detected for {doc.name}. Allowed quota={allowed}")
    # If allowed <= 0, treat as "no late allowed" (i.e., a late present becomes Half Day)
    # Here: 0 => none allowed
    if allowed <= 0:
        # No late allowed: if currently Present, downgrade
        if status_lc == "present":
            doc.status = "Half Day"
            if hasattr(doc, "half_day_status") and not doc.half_day_status:
                doc.half_day_status = "Absent"
        return

    period_start, period_end = _payroll_period_for_date(doc.attendance_date)

    # Count submitted late-within-grace days in this open payroll window
    used_other = frappe.db.sql(
        """
        SELECT COUNT(*)
        FROM `tabAttendance`
        WHERE employee=%s
          AND docstatus=1
          AND attendance_date BETWEEN %s AND %s
          AND IFNULL(custom_late_entry_within_grace, 0) = 1
          AND status='Present'
          AND name != %s
        """,
        (doc.employee, period_start, period_end, doc.name or ""),
        )[0][0] or 0

    effective_used = used_other + 1

    logger.debug(
        f"[LATE-GRACE-QUOTA] emp={doc.employee} att={doc.name} date={doc.attendance_date} "
        f"period={period_start}..{period_end} used_other={used_other} effective={effective_used} allowed={allowed} "
        f"status_before={doc.status} in_time={doc.in_time}"
    )

    # Within quota: allow Present (only now, because guards passed)
    if effective_used <= allowed:
        logger.debug(f"[LATE-POLICY] Within quota: allowing Present status for {doc.name}") 
        if (doc.status or "").strip().lower() == "half day":
            doc.status = "Present"
            if hasattr(doc, "half_day_status"):
                doc.half_day_status = None
            if hasattr(doc, "custom_late_quota_override"):
                doc.custom_late_quota_override = 1
        return

    # Over quota: force Half Day (but never override Absent)
    if (doc.status or "").strip().lower() == "present":
        doc.status = "Half Day"
        if hasattr(doc, "half_day_status") and not doc.half_day_status:
            doc.half_day_status = "Absent"
        logger.debug(f"[LATE-POLICY] Late quota exceeded => downgraded Present to Half Day. att={doc.name}")