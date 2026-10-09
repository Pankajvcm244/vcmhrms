from calendar import monthrange
from datetime import date, timedelta
from datetime import datetime
import frappe
from frappe.utils import getdate
from vcmhrms.temp_worker.service import check_branch
from frappe.utils import get_datetime


@frappe.whitelist()
def get_month(month,branch):
    check_branch(branch)
    try: start=getdate(datetime.strptime(str(month),'%m-%Y').date().replace(day=1))
    except (TypeError,ValueError): frappe.throw('Enter month as MM-YYYY, for example 10-2026.')
    end=date(start.year,start.month,monthrange(start.year,start.month)[1])
    workers=frappe.get_list('Temp Worker',filters={'branch':branch,'status':'Active'},
        fields=['name','worker_name','staff_id','worker_role','department','branch','supervisor'],
        limit_page_length=0,order_by='worker_name')
    rosters=frappe.get_list('Temp Roster',filters={'branch':branch,
        'roster_date':['between',[start,end]]},fields=['name','worker','roster_date','day_type'],limit_page_length=0)
    for row in rosters:
        row['shifts']=[d.shift for d in frappe.get_doc('Temp Roster',row.name).duties]
    return dict(workers=workers,rosters=rosters,days=end.day)


@frappe.whitelist(methods=['POST'])
def allocate_range(worker,branch,from_date,to_date,day_type,shift=None,
                   planned_before_ot_hours=0,planned_after_ot_hours=0,ot_reason=None):
    check_branch(branch)
    person=frappe.get_doc('Temp Worker',worker)
    person.check_permission('read')
    start,end=getdate(from_date),getdate(to_date)
    if end<start or (end-start).days>61:
        frappe.throw('Choose a date range of at most 62 days.')
    if day_type not in ('Duty','Weekly Off'): frappe.throw('Invalid day type.')
    template=frappe.get_doc('Temp Shift',shift) if day_type=='Duty' else None
    day=start
    names=[]
    while day<=end:
        name=frappe.db.get_value('Temp Roster',{'worker':worker,'roster_date':day})
        doc=frappe.get_doc('Temp Roster',name) if name else frappe.new_doc('Temp Roster')
        doc.update(dict(worker=worker,branch=branch,roster_date=day,day_type=day_type))
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
