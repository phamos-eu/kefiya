frappe.ui.form.on('Purchase Invoice', {
    refresh: function(frm) {
        if (frm.is_new()) {
            // Clear all Attach and Attach Image fields dynamically
            frm.meta.fields
                .filter(df => ['Attach', 'Attach Image'].includes(df.fieldtype))
                .forEach(df => {
                    if (frm.doc[df.fieldname]) {
                        frm.set_value(df.fieldname, null);
                    }
                });

            // Remove any sidebar attachments
            if (frm.attachments) {
                let attachments = frm.attachments.get_attachments();
                attachments.forEach(attachment => {
                    frm.attachments.remove_attachment(attachment.name);
                });
            }
        }
    }
});
