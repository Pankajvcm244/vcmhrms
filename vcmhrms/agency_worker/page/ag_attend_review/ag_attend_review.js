frappe.pages['ag-attend-review'].on_page_load=function(wrapper) {
    const page=frappe.ui.make_app_page({parent:wrapper,title:__('Agency Attendance Review'),single_column:true});
    const from=page.add_field({fieldname:'from_date',label:'From Date',fieldtype:'Date',default:frappe.datetime.month_start(),reqd:1});
    const to=page.add_field({fieldname:'to_date',label:'To Date',fieldtype:'Date',default:frappe.datetime.get_today(),reqd:1});
    const budget_location=page.add_field({fieldname:'budget_location',label:'Budget Location',fieldtype:'Link',options:'Budget Location',default:'VRN'});
    const issue=page.add_field({fieldname:'issue',label:'Issue',fieldtype:'Select',options:'All\nMissing Punch\nHalf Day\nAbsent\nLate Entry\nEarly Exit',default:'All'});
    const reviewStatus=page.add_field({fieldname:'review_status',label:'Punch Approval',fieldtype:'Select',options:'Pending\nApproved\nAll',default:'Pending'});
    const body=$('<div class="mt-3" style="overflow:auto"></div>').appendTo(page.main);
    const esc=value=>frappe.utils.escape_html(String(value??''));
    let rows=[];
    async function load(){
        const {message}=await frappe.call({method:'vcmhrms.agency_worker.attend_review.get_exceptions',args:{
            from_date:from.get_value(),to_date:to.get_value(),budget_location:budget_location.get_value(),issue:issue.get_value(),
            review_status:reviewStatus.get_value()},freeze:true});
        rows=message||[];
        body.html(`<p>${rows.length} attendance exceptions. Managers see only workers assigned to them; System Manager is backup.</p>
        <table class="table table-bordered"><thead><tr><th>Worker</th><th>Date</th><th>Shift</th><th>First In</th><th>Last Out</th>
        <th>Worked</th><th>Required</th><th>Status</th><th>Flags</th><th>Punch Approval</th><th>Approved By</th><th>Action</th></tr></thead><tbody>`+
        rows.map((row,index)=>`<tr><td>${esc(row.worker_name)}</td><td>${esc(row.date)}</td><td>${esc(row.shift)}</td>
        <td>${esc(row.first_in)}</td><td>${esc(row.last_out)}</td><td>${row.worked_hours}</td><td>${row.required_hours}</td>
        <td>${esc(row.status)}</td><td>${esc(row.issues)}</td><td>${esc(row.punch_approval_status)}</td><td>${esc(row.punch_approved_by)}</td>
        <td><button class="btn btn-xs btn-primary correct" data-index="${index}">Correct / Exception</button>
        ${row.punch_approval_status==='Pending'?`<button class="btn btn-xs btn-default approve ml-1" data-index="${index}">Approve Punch</button>`:''}</td></tr>`).join('')+'</tbody></table>');
        body.find('.correct').on('click',function(){openDialog(rows[Number(this.dataset.index)]);});
        body.find('.approve').on('click',function(){approvePunch(rows[Number(this.dataset.index)]);});
    }
    function approvePunch(row){
        const dialog=new frappe.ui.Dialog({title:`Approve Punch Exception — ${row.worker_name}`,fields:[
            {fieldname:'details',fieldtype:'HTML',options:`<p><b>${esc(row.date)} · ${esc(row.shift)}</b><br>${esc(row.issues)}<br>Status: ${esc(row.status)}</p>`},
            {fieldname:'remarks',label:'Approval Remarks',fieldtype:'Small Text',reqd:1}
        ],primary_action_label:'Approve As Calculated',primary_action:async values=>{
            await frappe.call({method:'vcmhrms.agency_worker.attend_review.approve_punch',args:{attendance:row.attendance,
                roster_duty:row.roster_duty,remarks:values.remarks},freeze:true});
            dialog.hide(); frappe.show_alert('Punch exception approved.'); await load();
        }});dialog.show();
    }
    function openDialog(row){
        const dialog=new frappe.ui.Dialog({title:`Attendance Review — ${row.worker_name}`,fields:[
            {fieldname:'details',fieldtype:'HTML',options:`<p><b>${esc(row.date)} · ${esc(row.shift)}</b><br>${esc(row.issues)}</p>`},
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
    page.set_primary_action(__('Load Exceptions'),load);
    load();
};
