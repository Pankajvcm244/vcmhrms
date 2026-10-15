"""Biometric adapter for directionless punch times from the attendance provider."""
from datetime import datetime
from hashlib import sha256
from zoneinfo import ZoneInfo
import frappe
from frappe.utils import get_datetime, now_datetime, getdate, add_days
from vcmhrms.agency_worker.service import recalculate
from vcmhrms.agency_worker.period_lock import is_locked


def timestamp(value):
    parsed=datetime.fromisoformat(str(value).replace('Z','+00:00'))
    if parsed.tzinfo is None:
        # The current rollout is India-only. A timestamp without an offset is local
        # wall-clock time in the ERPNext site's configured timezone (Asia/Kolkata).
        return parsed.replace(microsecond=0)
    return parsed.astimezone(ZoneInfo(frappe.utils.get_system_timezone())).replace(tzinfo=None,microsecond=0)


@frappe.whitelist(methods=['POST'])
def receive_punches():
    allowed_roles = {'Agency Worker Integration', 'System Manager'}
    if frappe.session.user == 'Guest' or not (allowed_roles & set(frappe.get_roles())):
        frappe.throw('Agency Worker Integration or System Manager role required.', frappe.PermissionError)
    payload=frappe.request.get_json()
    return ingest_payload(payload)


def ingest_payload(payload, simulated=False):
    """Shared ingestion used by token API and the permission-checked Desk simulator."""
    if not isinstance(payload,dict) or not isinstance(payload.get('punches'),list):
        frappe.throw('Expected an object containing a punches array.')
    if len(payload['punches']) > 500:
        frappe.throw('Maximum 500 punches per request.')
    results=[]
    affected=set()
    for index,row in enumerate(payload['punches']):
        savepoint=f'workforce_{index}'
        frappe.db.savepoint(savepoint)
        try:
            staff_id=str(row.get('staff_id') or '').strip()
            biometric_code=str(row.get('biometric_code') or '').strip()
            if not staff_id or not biometric_code or not row.get('punch_time'):
                frappe.throw('staff_id, biometric_code and punch_time are required.')
            worker=frappe.db.get_value('Agency Worker',
                {'staff_id':staff_id,'smartoffice_id':biometric_code},'name')
            if not worker:
                frappe.throw('Staff ID and Biometric Code do not identify the same Agency Worker.')
            time=timestamp(row.get('punch_time'))
            if time>now_datetime(): frappe.throw('Future punch not accepted.')
            # Stable internal event identity makes provider retries idempotent without
            # requiring the provider to understand or supply an event ID.
            event_id='BIO-'+sha256(f'{staff_id}|{biometric_code}|{time.isoformat()}'.encode()).hexdigest()[:40]
            existing=frappe.db.get_value('Agency Punch',{'event_id':event_id},['name','worker','punch_time'],as_dict=True)
            if existing:
                if existing.worker!=worker or get_datetime(existing.punch_time)!=time:
                    frappe.throw('Event ID already exists with different worker or timestamp.')
                results.append(dict(event_id=event_id,status='duplicate'))
                continue
            person=frappe.get_doc('Agency Worker',worker)
            if getdate(time)<getdate(person.active_from) or (person.active_until and getdate(time)>getdate(person.active_until)):
                frappe.throw('Punch falls outside worker engagement dates.')
            nearby_rosters=frappe.get_all('Agency Roster',filters={'worker':worker,
                'roster_date':['between',[add_days(getdate(time),-1),add_days(getdate(time),1)]]},
                fields=['roster_date','budget_location'])
            if any(is_locked(row.roster_date,row.budget_location) for row in nearby_rosters):
                frappe.throw('Punch affects a locked attendance period and cannot be accepted.')
            doc=frappe.get_doc(dict(doctype='Agency Punch',event_id=event_id,worker=worker,staff_id=person.staff_id,
                punch_time=time,device_id=row.get('device_id'),source_location=row.get('location'),received_at=now_datetime(),
                is_simulated=int(simulated)))
            doc.flags.workforce_ingestion=True
            doc.insert(ignore_permissions=True)
            affected.add((worker,getdate(time)))
            results.append(dict(event_id=event_id,status='created'))
        except Exception as error:
            frappe.db.rollback(save_point=savepoint)
            results.append(dict(row=index+1,status='failed',message=str(error)))
    failed=any(r['status']=='failed' for r in results)
    # Recalculate only rosters near newly inserted punches. This covers overnight duties
    # without scanning every historical roster after every provider batch.
    roster_names=set()
    for worker,punch_date in affected:
        roster_names.update(frappe.get_all('Agency Roster',filters={'worker':worker,
            'roster_date':['between',[add_days(punch_date,-1),add_days(punch_date,1)]]},pluck='name'))
    roster_names=sorted(roster_names)
    # Keep each job bounded. A maximum-size provider batch can affect hundreds of
    # rosters, and one large job may exceed the worker timeout after all punches
    # have already committed.
    for offset in range(0,len(roster_names),50):
        chunk=roster_names[offset:offset+50]
        job_key=sha256('|'.join(chunk).encode()).hexdigest()[:12]
        frappe.enqueue('vcmhrms.agency_worker.api.recalculate_rosters',
            roster_names=chunk,enqueue_after_commit=True,
            job_name=f'Agency punch recalculation {job_key}')
    return dict(success=not failed,results=results)


def recalculate_rosters(roster_names):
    recalculated=failed=0
    for name in roster_names:
        try:
            recalculate(frappe.get_doc('Agency Roster',name))
            recalculated+=1
        except Exception:
            failed+=1
            frappe.log_error(frappe.get_traceback(),f'Agency punch recalculation failed: {name}')
    return dict(recalculated=recalculated,failed=failed)


def finalize_pending_attendance():
    """Finalize duties whose capture windows have closed, including no-punch absences."""
    rows=frappe.db.sql('''select distinct a.roster
        from `tabAgency Attendance` a
        join `tabAgency Attendance Duty` d on d.parent=a.name
        join `tabAgency Roster Duty` rd on rd.name=d.roster_duty
        join `tabAgency Shift` s on s.name=rd.shift
        where d.status='Pending'
          and timestampadd(minute,
                greatest(coalesce(rd.planned_after_ot_hours,0),coalesce(s.max_after_ot_capture_hours,0))*60
                    + coalesce(s.late_minutes,0),rd.end_datetime)<=%s
        limit 2000''',now_datetime(),as_dict=True)
    recalculated=0
    failed=0
    for row in rows:
        try:
            recalculate(frappe.get_doc('Agency Roster',row.roster))
            recalculated+=1
        except Exception:
            failed+=1
            frappe.log_error(frappe.get_traceback(),f'Agency Attendance finalization failed: {row.roster}')
    return dict(found=len(rows),recalculated=recalculated,failed=failed)


@frappe.whitelist(methods=['POST'])
def run_pending_finalization():
    """Manual test/operations trigger; the same function also runs daily at 04:00 and 16:00."""
    if frappe.session.user=='Guest' or not ({'System Manager','Agency Worker Manager'} & set(frappe.get_roles())):
        frappe.throw('Agency Worker Manager or System Manager role required.',frappe.PermissionError)
    return finalize_pending_attendance()

