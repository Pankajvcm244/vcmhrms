// Copyright (c) 2026, Pankaj Sharma and contributors
// For license information, please see license.txt

frappe.pages['ag-ot-approval'].on_page_load=function(wrapper) {
    const page=frappe.ui.make_app_page({parent:wrapper,title:__('Agency OT Approval'),single_column:true});
    const from=page.add_field({fieldname:'from_date',label:'From Date',fieldtype:'Date',default:frappe.datetime.month_start(),reqd:1});
    const to=page.add_field({fieldname:'to_date',label:'To Date',fieldtype:'Date',default:frappe.datetime.get_today(),reqd:1});
    const budget_location=page.add_field({fieldname:'budget_location',label:'Budget Location',fieldtype:'Link',options:'Budget Location',default:'VRN'});
    const status=page.add_field({fieldname:'status',label:'Status',fieldtype:'Select',options:'Pending\nApproved\nRejected\nAll',default:'Pending'});
    const body=$('<div class="mt-3" style="overflow:auto"></div>').appendTo(page.main);
    const esc=value=>frappe.utils.escape_html(String(value??''));
    let rows=[];
    async function load() {
        const {message}=await frappe.call({method:'vcmhrms.agency_worker.ot_approval.get_pending_ot',args:{
            from_date:from.get_value(),to_date:to.get_value(),budget_location:budget_location.get_value(),status:status.get_value()},freeze:true});
        rows=message||[];
        body.html(`<p>${rows.length} OT duties. Managers see only workers assigned to them as Supervisor.</p><table class="table table-bordered"><thead><tr>
            <th><input type="checkbox" class="select-all"></th><th>Worker</th><th>Staff ID</th><th>Supervisor</th><th>Date</th><th>Shift</th>
            <th>Planned Before</th><th>Actual Before</th><th>Approve Before</th>
            <th>Planned After</th><th>Actual After</th><th>Approve After</th><th>OT Reason</th><th>Status</th>
            <th>Approved By</th><th>Approved At</th><th>Approval Remarks</th></tr></thead><tbody>`+
            rows.map((row,index)=>{const locked=row.status!=='Pending';return `<tr><td><input type="checkbox" class="select-row" data-index="${index}" ${locked?'disabled':''}></td>
            <td>${esc(row.worker_name)}</td><td>${esc(row.staff_id)}</td><td>${esc(row.supervisor_name)}</td><td>${esc(row.date)}</td><td>${esc(row.shift)}</td>
            <td>${row.planned_before}</td><td>${row.actual_before}</td><td><input class="form-control before" data-index="${index}" type="number" min="0" max="${row.actual_before}" step="0.25" value="${row.approved_before}" ${locked?'disabled':''}></td>
            <td>${row.planned_after}</td><td>${row.actual_after}</td><td><input class="form-control after" data-index="${index}" type="number" min="0" max="${row.actual_after}" step="0.25" value="${row.approved_after}" ${locked?'disabled':''}></td>
            <td>${esc(row.reason)}</td><td>${esc(row.status)}</td><td>${esc(row.approved_by)}</td><td>${esc(row.approved_at)}</td><td>${esc(row.remarks)}</td></tr>`;}).join('')+'</tbody></table>');
        body.find('.select-all').on('change',function(){body.find('.select-row:not(:disabled)').prop('checked',this.checked);});
    }
    function selected() {
        return body.find('.select-row:checked').map(function(){
            const index=Number(this.dataset.index),row={...rows[index]};
            row.approved_before=Number(body.find(`.before[data-index="${index}"]`).val()||0);
            row.approved_after=Number(body.find(`.after[data-index="${index}"]`).val()||0);
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
    page.add_inner_button(__('Approve Planned'),()=>approve('Planned'));
    page.add_inner_button(__('Approve Actual'),()=>approve('Actual'));
    page.add_inner_button(__('Save Custom'),()=>approve('Custom'));
    page.add_inner_button(__('Reject'),()=>approve('Reject'));
    load();
};
