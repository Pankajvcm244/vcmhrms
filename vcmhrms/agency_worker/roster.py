from calendar import monthrange
from datetime import date, timedelta
from datetime import datetime
import frappe
from frappe.utils import getdate
from vcmhrms.agency_worker.service import check_budget_location
from frappe.utils import get_datetime


@frappe.whitelist()
def get_month(month,budget_location):
    check_budget_location(budget_location)
    try: start=getdate(datetime.strptime(str(month),'%m-%Y').date().replace(day=1))
    except (TypeError,ValueError): frappe.throw('Enter month as MM-YYYY, for example 10-2026.')
    end=date(start.year,start.month,monthrange(start.year,start.month)[1])
    workers=frappe.get_list('Agency Worker',filters={'budget_location':budget_location,'status':'Active'},
        fields=['name','worker_name','staff_id','agency','worker_role','department','budget_location'],
        limit_page_length=0,order_by='worker_name')
    rosters=frappe.get_list('Agency Roster',filters={'budget_location':budget_location,
        'roster_date':['between',[start,end]]},fields=['name','worker','roster_date','day_type'],limit_page_length=0)
    for row in rosters:
        row['shifts']=[d.shift for d in frappe.get_doc('Agency Roster',row.name).duties]
    return dict(workers=workers,rosters=rosters,days=end.day)


@frappe.whitelist(methods=['POST'])
def allocate_range(worker,budget_location,from_date,to_date,day_type,shift=None,
                   planned_before_ot_hours=0,planned_after_ot_hours=0,ot_reason=None):
    check_budget_location(budget_location)
    person=frappe.get_doc('Agency Worker',worker)
    person.check_permission('read')
    start,end=getdate(from_date),getdate(to_date)
    if end<start or (end-start).days>61:
        frappe.throw('Choose a date range of at most 62 days.')
    if day_type not in ('Duty','Weekly Off'): frappe.throw('Invalid day type.')
    template=frappe.get_doc('Agency Shift',shift) if day_type=='Duty' else None
    day=start
    names=[]
    while day<=end:
        name=frappe.db.get_value('Agency Roster',{'worker':worker,'roster_date':day})
        doc=frappe.get_doc('Agency Roster',name) if name else frappe.new_doc('Agency Roster')
        doc.update(dict(worker=worker,budget_location=budget_location,roster_date=day,day_type=day_type))
        doc.set('duties',[])
        if template:
            begin=get_datetime(f'{day} {template.start_time}')
            finish=get_datetime(f'{day} {template.end_time}')
            if finish<=begin: finish+=timedelta(days=1)
            doc.append('duties',dict(shift=shift,start_datetime=begin,end_datetime=finish,
                planned_before_ot_hours=float(planned_before_ot_hours or 0),
                planned_after_ot_hours=float(planned_after_ot_hours or 0),ot_reason=ot_reason))
        doc.save()
        names.append(doc.name)
        day+=timedelta(days=1)
    return names


@frappe.whitelist(methods=['POST'])
def allocate_bulk(workers,budget_location,from_date,to_date,shift,weekly_off=None,
                  existing_action='Skip Existing'):
    """Queue the common roster case for many workers; detailed exceptions stay in the grid."""
    check_budget_location(budget_location)
    workers=frappe.parse_json(workers) if isinstance(workers,str) else workers
    if not isinstance(workers,list) or not workers or len(workers)>200:
        frappe.throw('Select between 1 and 200 workers.')
    workers=list(dict.fromkeys(str(worker) for worker in workers))
    start,end=getdate(from_date),getdate(to_date)
    if end<start or (end-start).days>61:
        frappe.throw('Choose a date range of at most 62 days.')
    if existing_action not in ('Skip Existing','Replace Existing'):
        frappe.throw('Invalid existing-roster action.')
    weekdays={'Monday','Tuesday','Wednesday','Thursday','Friday','Saturday','Sunday'}
    if weekly_off and weekly_off not in weekdays:
        frappe.throw('Invalid weekly-off day.')
    frappe.get_doc('Agency Shift',shift).check_permission('read')
    for worker in workers:
        person=frappe.get_doc('Agency Worker',worker)
        person.check_permission('read')
        if person.budget_location!=budget_location or person.status!='Active':
            frappe.throw(f'{person.worker_name} is not an active worker in {budget_location}.')
    requested_by=frappe.session.user
    arguments=dict(workers=workers,budget_location=budget_location,from_date=str(start),to_date=str(end),shift=shift,
        weekly_off=weekly_off,existing_action=existing_action,requested_by=requested_by)
    # A single worker is quick enough to complete in the request and gives testers an
    # immediate, visible result. Larger batches use Frappe's standard worker queue.
    if len(workers)==1:
        result=allocate_bulk_job(**arguments)
        return dict(mode='completed',queued=0,result=result,from_date=start,to_date=end)
    job=frappe.enqueue('vcmhrms.agency_worker.roster.allocate_bulk_job',queue='default',timeout=7200,
        job_name=f'Agency Bulk Roster: {budget_location}: {start} to {end}',
        workers=workers,budget_location=budget_location,from_date=str(start),to_date=str(end),
        shift=shift,weekly_off=weekly_off,existing_action=existing_action,requested_by=requested_by)
    return dict(mode='queued',queued=len(workers),job_id=getattr(job,'id',None),
                from_date=start,to_date=end)


def allocate_bulk_job(workers,budget_location,from_date,to_date,shift,weekly_off=None,
                      existing_action='Skip Existing',requested_by=None):
    start,end=getdate(from_date),getdate(to_date)
    template=frappe.get_doc('Agency Shift',shift)
    created=replaced=skipped=failed=0
    for worker in workers:
        day=start
        worker_created=worker_replaced=worker_skipped=0
        try:
            person=frappe.get_doc('Agency Worker',worker)
            while day<=end:
                if day<getdate(person.active_from) or (person.active_until and day>getdate(person.active_until)):
                    worker_skipped+=1
                    day+=timedelta(days=1)
                    continue
                name=frappe.db.get_value('Agency Roster',{'worker':worker,'roster_date':day})
                if name and existing_action=='Skip Existing':
                    worker_skipped+=1
                    day+=timedelta(days=1)
                    continue
                doc=frappe.get_doc('Agency Roster',name) if name else frappe.new_doc('Agency Roster')
                day_type='Weekly Off' if weekly_off and day.strftime('%A')==weekly_off else 'Duty'
                doc.update(dict(worker=worker,budget_location=budget_location,roster_date=day,day_type=day_type))
                doc.set('duties',[])
                if day_type=='Duty':
                    begin=get_datetime(f'{day} {template.start_time}')
                    finish=get_datetime(f'{day} {template.end_time}')
                    if finish<=begin: finish+=timedelta(days=1)
                    doc.append('duties',dict(shift=shift,start_datetime=begin,end_datetime=finish,
                                             planned_before_ot_hours=0,planned_after_ot_hours=0))
                doc.save(ignore_permissions=True)
                if name: worker_replaced+=1
                else: worker_created+=1
                day+=timedelta(days=1)
            frappe.db.commit()
            created+=worker_created
            replaced+=worker_replaced
            skipped+=worker_skipped
        except Exception:
            frappe.db.rollback()
            failed+=1
            frappe.log_error(frappe.get_traceback(),f'Agency bulk roster failed: {worker}')
    if requested_by:
        frappe.publish_realtime('agency_roster_bulk_complete',dict(created=created,replaced=replaced,
            skipped=skipped,failed_workers=failed),user=requested_by)
    return dict(created=created,replaced=replaced,skipped=skipped,failed_workers=failed)

