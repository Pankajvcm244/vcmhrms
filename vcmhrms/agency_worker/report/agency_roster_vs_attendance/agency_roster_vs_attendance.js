frappe.query_reports['Agency Roster vs Attendance'] = {
    filters: [
        {fieldname:'from_date',label:'From Date',fieldtype:'Date',reqd:1,default:frappe.datetime.month_start()},
        {fieldname:'to_date',label:'To Date',fieldtype:'Date',reqd:1,default:frappe.datetime.get_today()},
        {fieldname:'budget_location',label:'Budget Location',fieldtype:'Link',options:'Budget Location',reqd:1,default:'VRN'}
    ]
};
