// Copyright (c) 2026, Pankaj Sharma and contributors
// For license information, please see license.txt

frappe.pages['ag-ot-approval'].on_page_load=function(wrapper) {
    const page=frappe.ui.make_app_page({parent:wrapper,title:__('Agency OT Approval'),single_column:true});
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
    const from=page.add_field({fieldname:'from_date',label:'From Date',fieldtype:'Date',default:frappe.datetime.obj_to_str(payrollStart),reqd:1,change:applyFilters});
    const to=page.add_field({fieldname:'to_date',label:'To Date',fieldtype:'Date',default:frappe.datetime.obj_to_str(payrollEnd),reqd:1,change:applyFilters});
    const budget_location=page.add_field({fieldname:'budget_location',label:'Budget Location',fieldtype:'Link',options:'Budget Location',default:'VRN',change:applyFilters});
    const agency=page.add_field({fieldname:'agency',label:'Agency',fieldtype:'Link',options:'Agency',change:applyFilters});
    const worker=page.add_field({fieldname:'worker',label:'Agency Staff',fieldtype:'Link',options:'Agency Worker',change:applyFilters});
    const status=page.add_field({fieldname:'status',label:'Status',fieldtype:'Select',options:'Pending\nApproved\nRejected\nAll',default:'Pending',change:applyFilters});
    const body=$('<div class="mt-3" style="overflow:auto"></div>').appendTo(page.main);
    const esc=value=>frappe.utils.escape_html(String(value??''));
    const displayDate=value=>{
        if(!value)return '';
        const [year,month,day]=String(value).slice(0,10).split('-');
        return day&&month&&year?`${day}-${month}-${year}`:String(value);
    };
    const displayDatetime=value=>{
        if(!value)return '';
        const text=String(value).replace('T',' ');
        return `${displayDate(text.slice(0,10))} ${text.slice(11,19)}`.trim();
    };
    let rows=[];
    let loadRequest=0;
    function exportCsv(){
        if(!rows.length)return frappe.msgprint(__('There are no loaded OT rows to export.'));
        const columns=[['Agency Staff Name','worker_name'],['Agency Staff ID','staff_id'],['Agency','agency'],['Supervisor','supervisor_name'],
            ['Date','date'],['Shift','shift'],['First In','first_in'],['Last Out','last_out'],
            ['Total Shift Hours','required_hours_display'],['Total Worked Hours','worked_hours_display'],
            ['OT Hours','actual_ot_display'],['Approved OT','approved_ot_display'],
            ['OT Reason','reason'],['Status','status'],['Approved By','approved_by'],
            ['Approved At','approved_at'],['Approval Remarks','remarks']];
        const quote=value=>`"${String(value??'').replaceAll('"','""')}"`;
        const csv=[columns.map(column=>quote(column[0])).join(','),...rows.map(row=>
            columns.map(column=>quote(row[column[1]])).join(','))].join('\r\n');
        const link=document.createElement('a');
        link.href=URL.createObjectURL(new Blob(['\uFEFF',csv],{type:'text/csv;charset=utf-8'}));
        link.download=`Agency OT Approval ${from.get_value()} to ${to.get_value()}.csv`;
        link.click(); URL.revokeObjectURL(link.href);
    }
    async function load() {
        const request=++loadRequest;
        body.html('<p class="text-muted">Applying OT filters…</p>');
        const {message}=await frappe.call({method:'vcmhrms.agency_worker.ot_approval.get_pending_ot',args:{
            from_date:from.get_value(),to_date:to.get_value(),budget_location:budget_location.get_value(),
            agency:agency.get_value(),worker:worker.get_value(),status:status.get_value()}});
        if(request!==loadRequest)return;
        rows=message||[];
        body.html(`<p>${rows.length} OT duties. Managers see only workers assigned to them as Supervisor.</p><table class="table table-bordered"><thead><tr>
            <th><input type="checkbox" class="select-all"></th><th>Worker</th><th>Staff ID</th><th>Agency</th><th>Supervisor</th><th>Date</th><th>Shift</th><th>First In</th><th>Last Out</th>
            <th>Total Shift Hours</th><th>Total Worked Hours</th><th>OT Hours</th><th>Approve OT</th><th>OT Reason</th><th>Status</th>
            <th>Approved By</th><th>Approved At</th><th>Approval Remarks</th></tr></thead><tbody>`+
            rows.map((row,index)=>{const locked=row.status!=='Pending';return `<tr><td><input type="checkbox" class="select-row" data-index="${index}" ${locked?'disabled':''}></td>
            <td>${esc(row.worker_name)}</td><td>${esc(row.staff_id)}</td><td>${esc(row.agency)}</td><td>${esc(row.supervisor_name)}</td><td>${esc(displayDate(row.date))}</td><td>${esc(row.shift)}</td><td>${esc(displayDatetime(row.first_in))}</td><td>${esc(displayDatetime(row.last_out))}</td>
            <td>${esc(row.required_hours_display)}</td><td>${esc(row.worked_hours_display)}</td><td>${esc(row.actual_ot_display)}</td><td><input class="form-control approved-total" data-index="${index}" type="text" placeholder="HH:MM" value="${esc(row.approved_ot_display)}" ${locked?'disabled':''}></td>
            <td>${esc(row.reason)}</td><td>${esc(row.status)}</td><td>${esc(row.approved_by)}</td><td>${esc(row.approved_at)}</td><td>${esc(row.remarks)}</td></tr>`;}).join('')+'</tbody></table>');
        body.find('.select-all').on('change',function(){body.find('.select-row:not(:disabled)').prop('checked',this.checked);});
    }
    function parseDuration(value) {
        const match=String(value||'').trim().match(/^(\d{1,3}):([0-5]\d)$/);
        if(!match)frappe.throw(__('Enter approval duration as HH:MM, for example 01:30.'));
        return Number(match[1])+Number(match[2])/60;
    }
    function selected() {
        return body.find('.select-row:checked').map(function(){
            const index=Number(this.dataset.index),row={...rows[index]};
            row.approved_total=parseDuration(body.find(`.approved-total[data-index="${index}"]`).val());
            return row;
        }).get();
    }
    function approve(decision) {
        const chosen=selected();
        if(!chosen.length)return frappe.msgprint('Select at least one OT row.');
        const dialog=new frappe.ui.Dialog({title:`${decision} OT`,fields:[{fieldname:'remarks',label:'Approval Remarks',fieldtype:'Small Text',reqd:1}],
            primary_action_label:'Confirm',primary_action:async values=>{
                await frappe.call({method:'vcmhrms.agency_worker.ot_approval.approve_ot',args:{rows:JSON.stringify(chosen),remarks:values.remarks,decision},freeze:true});
                dialog.hide(); frappe.show_alert('OT approval updated.'); await load();
            }}); dialog.show();
    }
    page.set_primary_action(__('Load OT'),load);
    page.add_inner_button(__('Approve OT'),()=>approve('Actual'));
    page.add_inner_button(__('Approve Entered OT'),()=>approve('Custom'));
    page.add_inner_button(__('Reject'),()=>approve('Reject'));
    page.add_inner_button(__('Export CSV'),exportCsv);
    filtersReady=true;
    load();
};


