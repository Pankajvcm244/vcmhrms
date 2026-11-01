// Copyright (c) 2026, Pankaj Sharma and contributors
// For license information, please see license.txt

frappe.pages['ag-attend-review'].on_page_load=function(wrapper) {
    const page=frappe.ui.make_app_page({parent:wrapper,title:__('Agency Attendance Review'),single_column:true});
    let filtersReady=false;
    let reloadTimer=null;
    const applyFilters=()=>{
        if(!filtersReady)return;
        clearTimeout(reloadTimer);
        reloadTimer=setTimeout(()=>load(),250);
    };
    const today=new Date(`${frappe.datetime.get_today()}T00:00:00`);
    const payrollEnd=new Date(today.getFullYear(),today.getMonth()+(today.getDate()>=26?1:0),25);
    const payrollStart=new Date(payrollEnd.getFullYear(),payrollEnd.getMonth()-1,26);
    const dateValue=value=>frappe.datetime.obj_to_str(value);
    const from=page.add_field({fieldname:'from_date',label:'From Date',fieldtype:'Date',default:dateValue(payrollStart),reqd:1,change:applyFilters});
    const to=page.add_field({fieldname:'to_date',label:'To Date',fieldtype:'Date',default:dateValue(payrollEnd),reqd:1,change:applyFilters});
    const budget_location=page.add_field({fieldname:'budget_location',label:'Budget Location',fieldtype:'Link',options:'Budget Location',default:'VRN',change:applyFilters});
    const worker=page.add_field({fieldname:'worker',label:'Agency Staff',fieldtype:'Link',options:'Agency Worker',change:applyFilters});
    const agency=page.add_field({fieldname:'agency',label:'Agency',fieldtype:'Link',options:'Agency',change:applyFilters});
    const shift=page.add_field({fieldname:'shift',label:'Shift',fieldtype:'Link',options:'Agency Shift',change:applyFilters});
    const issue=page.add_field({fieldname:'issue',label:'Issue',fieldtype:'Select',options:'All\nMissing Punch\nHalf Day\nAbsent\nLate Entry\nEarly Exit',default:'All',change:applyFilters});
    const reviewStatus=page.add_field({fieldname:'review_status',label:'Punch Approval',fieldtype:'Select',options:'Pending\nApproved\nAll',default:'Pending',change:applyFilters});
    const body=$('<div class="mt-3" style="overflow:auto"></div>').appendTo(page.main);
    const esc=value=>frappe.utils.escape_html(String(value??''));
    const displayDate=value=>{
        if (!value) return '';
        const [year,month,day]=String(value).slice(0,10).split('-');
        return day&&month&&year?`${day}-${month}-${year}`:String(value);
    };
    const displayDatetime=value=>{
        if (!value) return '';
        const text=String(value).replace('T',' ');
        return `${displayDate(text.slice(0,10))} ${text.slice(11,19)}`.trim();
    };
    let rows=[];
    let loadRequest=0;
    function exportCsv(){
        if(!rows.length)return frappe.msgprint(__('There are no loaded attendance rows to export.'));
        const columns=[['Agency Staff Name','worker_name'],['Agency Staff ID','staff_id'],['Agency','agency'],
            ['Date','date'],['Shift','shift'],['First In','first_in'],['Last Out','last_out'],
            ['Worked Hours','worked_hours'],['Required Hours','required_hours'],['Status','status'],
            ['Flags','issues'],['Punch Approval','punch_approval_status'],['Approved By','punch_approved_by'],
            ['Approval Remarks','punch_approval_remarks']];
        const quote=value=>`"${String(value??'').replaceAll('"','""')}"`;
        const csv=[columns.map(column=>quote(column[0])).join(','),...rows.map(row=>
            columns.map(column=>quote(row[column[1]])).join(','))].join('\r\n');
        const link=document.createElement('a');
        link.href=URL.createObjectURL(new Blob(['\uFEFF',csv],{type:'text/csv;charset=utf-8'}));
        link.download=`Agency Attendance Review ${from.get_value()} to ${to.get_value()}.csv`;
        link.click(); URL.revokeObjectURL(link.href);
    }
    async function load(){
        const request=++loadRequest;
        body.html('<p class="text-muted">Applying attendance filters…</p>');
        const {message}=await frappe.call({method:'vcmhrms.agency_worker.attend_review.get_exceptions',args:{
            from_date:from.get_value(),to_date:to.get_value(),budget_location:budget_location.get_value(),worker:worker.get_value(),
            agency:agency.get_value(),shift:shift.get_value(),issue:issue.get_value(),
            review_status:reviewStatus.get_value()}});
        if(request!==loadRequest)return;
        rows=message||[];
        body.html(`<p>${rows.length} attendance exceptions. Managers see only workers assigned to them; System Manager is backup.</p>
        <table class="table table-bordered"><thead><tr><th>Worker</th><th>Date</th><th>Shift</th><th>First In</th><th>Last Out</th>
        <th>Worked</th><th>Required</th><th>Status</th><th>Flags</th><th>Punch Approval</th><th>Approved By</th><th>Action</th></tr></thead><tbody>`+
        rows.map((row,index)=>`<tr><td>${esc(row.worker_name)}</td><td>${esc(displayDate(row.date))}</td><td>${esc(row.shift)}</td>
        <td>${esc(displayDatetime(row.first_in))}</td><td>${esc(displayDatetime(row.last_out))}</td><td>${row.worked_hours}</td><td>${row.required_hours}</td>
        <td>${esc(row.status)}</td><td>${esc(row.issues)}</td><td>${esc(row.punch_approval_status)}</td><td>${esc(row.punch_approved_by)}</td>
        <td><button class="btn btn-xs btn-primary correct" data-index="${index}">Correct / Exception</button>
        ${row.punch_approval_status==='Pending'?`<button class="btn btn-xs btn-default approve ml-1" data-index="${index}">Approve Punch</button>`:''}</td></tr>`).join('')+'</tbody></table>');
        body.find('.correct').on('click',function(){openDialog(rows[Number(this.dataset.index)]);});
        body.find('.approve').on('click',function(){approvePunch(rows[Number(this.dataset.index)]);});
    }
    function approvePunch(row){
        const dialog=new frappe.ui.Dialog({title:`Approve Punch Exception — ${row.worker_name}`,fields:[
            {fieldname:'details',fieldtype:'HTML',options:`<p><b>${esc(displayDate(row.date))} · ${esc(row.shift)}</b><br>${esc(row.issues)}<br>Status: ${esc(row.status)}</p>`},
            {fieldname:'remarks',label:'Approval Remarks',fieldtype:'Small Text',reqd:1}
        ],primary_action_label:'Approve As Calculated',primary_action:async values=>{
            await frappe.call({method:'vcmhrms.agency_worker.attend_review.approve_punch',args:{attendance:row.attendance,
                roster_duty:row.roster_duty,remarks:values.remarks},freeze:true});
            dialog.hide(); frappe.show_alert('Punch exception approved.'); await load();
        }});dialog.show();
    }
    function openDialog(row){
        const dialog=new frappe.ui.Dialog({title:`Attendance Review — ${row.worker_name}`,fields:[
            {fieldname:'details',fieldtype:'HTML',options:`<p><b>${esc(displayDate(row.date))} · ${esc(row.shift)}</b><br>${esc(row.issues)}</p>`},
            {fieldname:'corrected_first_in',label:'Corrected First In',fieldtype:'Datetime',default:row.corrected_first_in||row.first_in},
            {fieldname:'corrected_last_out',label:'Corrected Last Out',fieldtype:'Datetime',default:row.corrected_last_out||row.last_out},
            {fieldname:'status_override',label:'Status Exception',fieldtype:'Select',options:'\nPresent\nHalf Day\nAbsent',default:row.status_override},
            {fieldname:'reason',label:'Correction / Exception Reason',fieldtype:'Small Text',reqd:1,default:row.exception_reason}
        ],primary_action_label:'Apply and Recalculate',primary_action:async values=>{
            await frappe.call({method:'vcmhrms.agency_worker.attend_review.apply_exception',args:{attendance:row.attendance,
                roster_duty:row.roster_duty,reason:values.reason,corrected_first_in:values.corrected_first_in,
                corrected_last_out:values.corrected_last_out,status_override:values.status_override},freeze:true});
            dialog.hide(); frappe.show_alert('Attendance corrected and recalculated.'); await load();
        }});dialog.show();
    }
    page.set_secondary_action(__('Process Attendance'),async()=>{
        const {message}=await frappe.call({method:'vcmhrms.agency_worker.api.run_pending_finalization',type:'POST',freeze:true});
        frappe.msgprint(__(`Found ${message.found}; recalculated ${message.recalculated}; failed ${message.failed}.`));
        await load();
    });
    page.add_inner_button(__('Export CSV'),exportCsv);
    page.set_primary_action(__('Load Exceptions'),load);
    filtersReady=true;
    load();
};


