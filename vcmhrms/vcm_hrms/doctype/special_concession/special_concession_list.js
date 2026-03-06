frappe.listview_settings['Special Concession'] = {

    get_indicator: function(doc) {

        if (doc.status === "Approved") {
            return ["Approved", "green", "status,=,Approved"];
        }

        if (doc.status === "Waiting for Approval") {
            return ["Waiting for Approval", "orange", "status,=,Waiting for Approval"];
        }

        if (doc.status === "Rejected") {
            return ["Rejected", "red", "status,=,Rejected"];
        }
    }
};