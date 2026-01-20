frappe.ui.form.on('Purchase Invoice', {
    refresh: function(frm) {
        if (frm.is_new()) {
            frm.set_value('originalbeleg_hochladen', null);
        }
    }
});
