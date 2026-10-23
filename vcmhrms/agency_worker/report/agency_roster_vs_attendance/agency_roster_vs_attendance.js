frappe.query_reports['Agency Roster vs Attendance'] = {
    filters: [
        {fieldname:'from_date',label:'From Date',fieldtype:'Date',reqd:1,default:frappe.datetime.month_start()},
        {fieldname:'to_date',label:'To Date',fieldtype:'Date',reqd:1,default:frappe.datetime.get_today()},
        {fieldname:'budget_location',label:'Budget Location',fieldtype:'Link',options:'Budget Location',reqd:1,default:'VRN'},
        {fieldname:'agency',label:'Agency',fieldtype:'Link',options:'Agency'},
        {fieldname:'department',label:'Department',fieldtype:'Select',options:`
HKM- Operation Housekeeping
TSF- Matchless Gift Shop
HKMV- Operation Security
TSF- Braj Nidhi Guest House
HKMV- Borewell
TSF- Annakoot
Pest Control
Maintenance
MK Maintenance
RO Maintenance
MK Security`},
        {fieldname:'worker',label:'Agency Staff Name',fieldtype:'Link',options:'Agency Worker',
            get_query:()=>({filters:{budget_location:frappe.query_report.get_filter_value('budget_location'),
                agency:frappe.query_report.get_filter_value('agency') || undefined,
                department:frappe.query_report.get_filter_value('department') || undefined,status:'Active'}})},
        {fieldname:'shift',label:'Shift',fieldtype:'Link',options:'Agency Shift'},
        {fieldname:'day_type',label:'Duty / Off',fieldtype:'Select',options:`\nDuty\nWeekly Off`},
        {fieldname:'status',label:'Attendance Status',fieldtype:'Select',options:`\nPending\nNeeds Review\nPresent\nHalf Day\nAbsent\nWeekly Off`}
    ]
};
