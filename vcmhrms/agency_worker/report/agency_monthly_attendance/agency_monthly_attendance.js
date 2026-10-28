// Copyright (c) 2026, Pankaj Sharma and contributors
// For license information, please see license.txt

frappe.query_reports['Agency Monthly Attendance'] = {
    async onload(report) {
        const {message:years}=await frappe.call({
            method:'vcmhrms.agency_worker.report.agency_monthly_attendance.agency_monthly_attendance.get_payroll_years'
        });
        const yearFilter=report.get_filter('year');
        yearFilter.df.options=(years||[]).join('\n');
        yearFilter.refresh();
        report.page.add_inner_button(__('Attendance Review'),()=>frappe.set_route('ag-attend-review'));
        report.page.add_inner_button(__('OT Approval'),()=>frappe.set_route('ag-ot-approval'));
        if (frappe.user_roles.includes('System Manager') || frappe.user_roles.includes('Agency Worker Manager')) {
            report.page.add_inner_button(__('Lock Completed Period'),()=>frappe.confirm(
                __('Lock this 26th–25th attendance period? Nobody will be able to change its roster, attendance, OT or punch reviews.'),
                async ()=> {
                    const {message}=await frappe.call({method:'vcmhrms.agency_worker.period_lock.lock_period',type:'POST',
                        args:{month:`${frappe.query_report.get_filter_value('month').slice(0,2)}-${frappe.query_report.get_filter_value('year')}`,
                            budget_location:frappe.query_report.get_filter_value('budget_location')},freeze:true});
                    frappe.msgprint(message.already_locked?__('This period is already locked.'):__('Attendance period locked.'));
                }));
        }
    },
    filters: [
        {fieldname:'year',label:'Year',fieldtype:'Select',reqd:1,
            options:(()=>{const value=frappe.datetime.get_today(); return value.slice(0,4);})(),
            default:(()=>{const value=frappe.datetime.get_today(); return value.slice(0,4);})()},
        {fieldname:'month',label:'Month',fieldtype:'Select',reqd:1,
            options:'01 - January\n02 - February\n03 - March\n04 - April\n05 - May\n06 - June\n07 - July\n08 - August\n09 - September\n10 - October\n11 - November\n12 - December',
            default:(()=>{const value=frappe.datetime.get_today(); const names=['January','February','March','April','May','June','July','August','September','October','November','December']; const number=value.slice(5,7); return `${number} - ${names[Number(number)-1]}`;})()},
        {fieldname:'budget_location',label:'Budget Location',fieldtype:'Link',options:'Budget Location',default:'VRN',reqd:1},
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
        {fieldname:'supervisor',label:'Supervisor (blank = All)',fieldtype:'Link',options:'User',
            get_query:()=>({query:'vcmhrms.agency_worker.report.agency_monthly_attendance.agency_monthly_attendance.get_supervisors'})}
    ],
    formatter(value,row,column,data,default_formatter) {
        value=default_formatter(value,row,column,data);
        if (!column.fieldname.startsWith('day_')) return value;
        const raw=String(data[column.fieldname] || '');
        if ((data.holiday_fields||[]).includes(column.fieldname)) return `<span style="color:${data.holiday_colour || '#6f42c1'};font-weight:600">${value}</span>`;
        if (raw.startsWith('A') || raw.startsWith('NR')) return `<span style="color:#c0392b;font-weight:600">${value}</span>`;
        if (raw.startsWith('HD') || raw.startsWith('PD')) return `<span style="color:#b7791f;font-weight:600">${value}</span>`;
        if (raw.startsWith('P') || raw.includes('✓')) return `<span style="color:#26734d">${value}</span>`;
        return value;
    }
};
