import json
from datetime import timedelta
from uuid import uuid4
from zoneinfo import ZoneInfo
import frappe
from frappe.utils import cint, get_datetime, now_datetime, get_system_timezone
from vcmhrms.temp_worker.api import ingest_payload
from vcmhrms.temp_worker.service import check_branch


@frappe.whitelist(methods=['POST'])
def prepare_manual_punches(worker,punch_times):
    """Build preview JSON from Desk Datetime values; no records are written here."""
    roles=set(frappe.get_roles())
    if frappe.session.user=='Guest' or not roles & {'System Manager','ERP Manager'}:
        frappe.throw('ERP Manager or System Manager role required.',frappe.PermissionError)
    doc=frappe.get_doc('Temp Worker',worker)
    doc.check_permission('read')
    check_branch(doc.branch)
    if isinstance(punch_times,str):
        try: punch_times=json.loads(punch_times)
        except ValueError: frappe.throw('Invalid punch times JSON.')
    if not isinstance(punch_times,list) or not 1<=len(punch_times)<=500 or any(not value for value in punch_times):
        frappe.throw('Enter between 1 and 500 punch timestamps.')
    times=sorted(get_datetime(value) for value in punch_times)
    if any(time>now_datetime() for time in times):frappe.throw('Choose past punch times.')
    timezone=ZoneInfo(get_system_timezone())
    prefix='SIM-'+uuid4().hex
    punches=[dict(event_id=f'{prefix}-{index+1}',person_id=doc.smartoffice_id,
        punch_time=time.replace(tzinfo=timezone).isoformat(),device_id='SIMULATOR',location=doc.branch)
        for index,time in enumerate(times)]
    return dict(punches=punches,
        synced_through=(max(times)+timedelta(hours=3)).replace(tzinfo=timezone).isoformat())


@frappe.whitelist(methods=['POST'])
def submit_sample(payload, worker, finalize=0):
    roles=set(frappe.get_roles())
    if frappe.session.user=='Guest' or not roles & {'System Manager','ERP Manager'}:
        frappe.throw('ERP Manager or System Manager role required.',frappe.PermissionError)
    if isinstance(payload,str):
        if len(payload)>500000: frappe.throw('JSON payload is too large.')
        try:
            payload=json.loads(payload)
        except (ValueError,TypeError):
            frappe.throw('Invalid JSON.')
    if not isinstance(payload,dict) or not isinstance(payload.get('punches'),list):
        frappe.throw('Expected an object with a punches array.')
    if not 1<=len(payload['punches'])<=500:
        frappe.throw('Provide between 1 and 500 punches.')
    if not worker: frappe.throw('Select one Temp Worker.')
    doc=frappe.get_doc('Temp Worker',worker)
    doc.check_permission('read')
    check_branch(doc.branch)
    # Enforce one selected worker on the server, including uploaded/edited JSON.
    for row in payload['punches']:
        if not isinstance(row,dict): frappe.throw('Every punch must be a JSON object.')
        if str(row.get('person_id') or '').strip()!=doc.smartoffice_id:
            frappe.throw('Every punch must belong to the selected worker SmartOffice ID.')
    payload=dict(payload)
    if cint(finalize):
        if 'System Manager' not in roles:
            frappe.throw('Only System Manager can advance global sync completeness.',frappe.PermissionError)
        if not payload.get('synced_through'):
            frappe.throw('Provide synced_through to finalize the sample.')
    else:
        payload.pop('synced_through',None)
    return ingest_payload(payload,simulated=True)
