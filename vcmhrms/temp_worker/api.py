"""SmartOffice adapter: POST JSON {punches: [...], synced_through: optional ISO timestamp}."""
from datetime import datetime
from zoneinfo import ZoneInfo
import frappe
from frappe.utils import get_datetime, now_datetime, getdate, add_days
from vcmhrms.temp_worker.service import recalculate


def timestamp(value):
    parsed=datetime.fromisoformat(str(value).replace('Z','+00:00'))
    if parsed.tzinfo is None:
        frappe.throw('Timestamp must include its UTC offset, for example +05:30.')
    return parsed.astimezone(ZoneInfo(frappe.utils.get_system_timezone())).replace(tzinfo=None,microsecond=0)


@frappe.whitelist(methods=['POST'])
def receive_punches():
    if frappe.session.user == 'Guest' or 'Temp Worker Integration' not in frappe.get_roles():
        frappe.throw('Temp Worker Integration role required.',frappe.PermissionError)
    payload=frappe.request.get_json()
    return ingest_payload(payload)


def ingest_payload(payload, simulated=False):
    """Shared ingestion used by token API and the permission-checked Desk simulator."""
    if not isinstance(payload,dict) or not isinstance(payload.get('punches'),list):
        frappe.throw('Expected an object containing a punches array.')
    if len(payload['punches']) > 500:
        frappe.throw('Maximum 500 punches per request.')
    results=[]
    for index,row in enumerate(payload['punches']):
        savepoint=f'workforce_{index}'
        frappe.db.savepoint(savepoint)
        try:
            event_id=str(row.get('event_id') or '').strip()
            person_id=str(row.get('person_id') or '').strip()
            if not event_id or len(event_id)>140 or not person_id:
                frappe.throw('event_id and person_id are required; event_id maximum 140 characters.')
            worker=frappe.db.get_value('Temp Worker',{'smartoffice_id':person_id},'name')
            if not worker: frappe.throw('Unknown SmartOffice person_id; register worker and retry.')
            time=timestamp(row.get('punch_time'))
            if time>now_datetime(): frappe.throw('Future punch not accepted.')
            existing=frappe.db.get_value('Temp Punch',{'event_id':event_id},['name','worker','punch_time'],as_dict=True)
            if existing:
                if existing.worker!=worker or get_datetime(existing.punch_time)!=time:
                    frappe.throw('Event ID already exists with different worker or timestamp.')
                results.append(dict(event_id=event_id,status='duplicate'))
                continue
            person=frappe.get_doc('Temp Worker',worker)
            if getdate(time)<getdate(person.active_from) or (person.active_until and getdate(time)>getdate(person.active_until)):
                frappe.throw('Punch falls outside worker engagement dates.')
            doc=frappe.get_doc(dict(doctype='Temp Punch',event_id=event_id,worker=worker,
                punch_time=time,device_id=row.get('device_id'),source_location=row.get('location'),received_at=now_datetime(),
                is_simulated=int(simulated)))
            doc.flags.workforce_ingestion=True
            doc.insert(ignore_permissions=True)
            results.append(dict(event_id=event_id,status='created'))
        except Exception as error:
            frappe.db.rollback(save_point=savepoint)
            results.append(dict(row=index+1,status='failed',message=str(error)))
    failed=any(r['status']=='failed' for r in results)
    if payload.get('synced_through') and not failed:
        value=timestamp(payload['synced_through'])
        if value>now_datetime(): frappe.throw('Sync watermark cannot be in the future.')
        # Lock one persisted setting so simultaneous batches cannot move the watermark backwards.
        frappe.db.sql('select name from `tabDefaultValue` where defkey=%s for update',
                      'temp_worker_synced_through')
        old=frappe.db.get_default('temp_worker_synced_through')
        if not old or value>get_datetime(old):
            frappe.db.set_default('temp_worker_synced_through',str(value))
    # Recalculate all open rosters when watermark advances; delayed historical punches also recalculate.
    frappe.enqueue('vcmhrms.temp_worker.api.recalculate_all',enqueue_after_commit=True)
    return dict(success=not failed,results=results)


def recalculate_all():
    for name in frappe.get_all('Temp Roster',pluck='name'):
        recalculate(frappe.get_doc('Temp Roster',name))
