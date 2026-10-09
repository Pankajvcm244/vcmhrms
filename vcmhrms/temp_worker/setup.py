import frappe


def after_migrate():
    for role in ['Temp Worker Manager','Temp Worker Supervisor','Temp Worker Integration','ERP Manager']:
        if not frappe.db.exists('Role',role):
            frappe.get_doc(dict(doctype='Role',role_name=role,desk_access=int(role!='Temp Worker Integration'))).insert(ignore_permissions=True)
    frappe.db.add_unique('Temp Roster',['worker','roster_date'],constraint_name='unique_temp_worker_date')
    if not frappe.db.get_default('temp_worker_synced_through'):
        frappe.db.set_default('temp_worker_synced_through','1900-01-01 00:00:00')
    if frappe.db.has_column('Temp Worker','staff_id'):
        # Keep legacy workers distinct from biometric IDs until HR enters the real Staff ID.
        frappe.db.sql("""update `tabTemp Worker` set staff_id=concat('LEGACY-',name)
            where coalesce(staff_id,'')='' """)
    if frappe.db.has_column('Temp Punch','staff_id'):
        frappe.db.sql('''update `tabTemp Punch` p join `tabTemp Worker` w on w.name=p.worker
            set p.staff_id=w.staff_id where coalesce(p.staff_id,'')='' ''')
    if frappe.db.has_column('Temp Attendance','worker_code'):
        frappe.db.sql('''update `tabTemp Attendance` a join `tabTemp Worker` w on w.name=a.worker
            set a.worker_code=a.worker where coalesce(a.worker_code,'')='' ''')
        frappe.db.sql('''update `tabTemp Attendance` a join `tabTemp Worker` w on w.name=a.worker
            set a.staff_id=w.staff_id where coalesce(a.staff_id,'')='' ''')
        frappe.db.sql('''update `tabTemp Attendance` a set
            a.late_entry=exists(select 1 from `tabTemp Attendance Duty` d where d.parent=a.name and d.late_entry=1),
            a.early_exit=exists(select 1 from `tabTemp Attendance Duty` d where d.parent=a.name and d.early_exit=1),
            a.worked_hours=coalesce((select sum(d.worked_hours) from `tabTemp Attendance Duty` d where d.parent=a.name),0)''')
    # Preserve early pilot data: the original single OT value is treated as after-shift OT.
    if frappe.db.has_column('Temp Roster Duty','planned_ot_hours'):
        frappe.db.sql('''update `tabTemp Roster Duty`
            set planned_after_ot_hours=planned_ot_hours
            where coalesce(planned_after_ot_hours,0)=0 and coalesce(planned_ot_hours,0)>0''')
    if frappe.db.has_column('Temp Attendance Duty','ot_approval_status'):
        frappe.db.sql('''update `tabTemp Attendance Duty`
            set ot_approval_status='Not Required'
            where coalesce(planned_before_ot_hours,0)=0 and coalesce(actual_before_ot_hours,0)=0
              and coalesce(planned_after_ot_hours,0)=0 and coalesce(actual_after_ot_hours,0)=0
              and coalesce(ot_approval_status,'Pending')='Pending' ''')
    if frappe.db.has_column('Temp Attendance Duty','punch_approval_status'):
        frappe.db.sql('''update `tabTemp Attendance Duty`
            set punch_approval_status='Pending'
            where (coalesce(late_entry,0)=1 or coalesce(early_exit,0)=1
                   or status in ('Half Day','Absent','Needs Review'))
              and coalesce(punch_approval_status,'Not Required')='Not Required' ''')
